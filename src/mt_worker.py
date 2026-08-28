"""T3 MT worker: NLLB via CTranslate2, greedy, per-sentence, with the measured
fragment policy — digits pass through untranslated, hallucination detector on
every output (ratio > 1.25 / per-token < -0.40 / invented digits)."""
import queue
import re
import threading
import time

from src import config

_SENT_SPLIT = re.compile(r"(?<=[.!?。！？…])\s+")
_DIGITS_ONLY = re.compile(r"^[\d\s.,:;%\-+]+$")

from src import interjections


def _desubtitle(out):
    """Strip NLLB's subtitle-dialog artifact (measured on 180 legit
    translations: short inputs draw a leading '- ' and invented
    second-speaker segments like '- Espera um momento. - Não.'). Keeps the
    first speaker segment only."""
    s = out.strip()
    if s.startswith("- "):
        s = s[2:].strip()
    cut = s.find(" - ")
    if cut > 0:
        s = s[:cut].strip()
    return s


class MtWorker(threading.Thread):
    def __init__(self, on_log, on_translated):
        super().__init__(name="mt", daemon=True)
        self.on_log = on_log
        self.on_translated = on_translated
        self.q = queue.Queue()
        self._stopping = threading.Event()

    def submit(self, turn, text, src_entry, tgt_entry, ear):
        self.q.put((turn, text, src_entry, tgt_entry, ear))

    def stop(self):
        self._stopping.set()

    def run(self):
        t0 = time.time()
        import ctranslate2
        import transformers
        self._tok = transformers.AutoTokenizer.from_pretrained(
            f"{config.MODELS}/nllb-tokenizer", src_lang="eng_Latn")
        self._tr = ctranslate2.Translator(
            f"{config.MODELS}/nllb-600m-int8", device="cpu",
            compute_type="int8", inter_threads=config.MT_INTER_THREADS,
            intra_threads=config.MT_INTRA_THREADS)
        self._translate_one("Hello.", "eng_Latn", "spa_Latn")  # warm
        self.on_log(f"MT   NLLB loaded and warm in {time.time()-t0:.1f}s")

        while not self._stopping.is_set():
            try:
                turn, text, src, tgt, ear = self.q.get(timeout=0.25)
            except queue.Empty:
                continue
            if turn.cancelled:
                self.on_log(f"MT   turn#{turn.turn_id} cancelled — skipped")
                continue
            t1 = time.time()
            if src["code"] == tgt["code"]:
                out = text
            else:
                out = self._translate(text, src, tgt, turn.turn_id)
            if not out:
                turn.state = "mt_dropped"   # terminal: keeps the lag metric honest
                self.on_log(f"MT   turn#{turn.turn_id} nothing translatable")
                continue
            if turn.cancelled:
                self.on_log(f"MT   turn#{turn.turn_id} cancelled during MT")
                continue
            self.on_log(f'MT   turn#{turn.turn_id} -> "{out}" '
                        f"({time.time()-t1:.2f}s)")
            turn.state = "translated"
            self.on_translated(turn, out, tgt, ear)

    def _translate(self, text, src, tgt, tid):
        """All sentences of a chunk in ONE translate_batch call — the 2x2
        workers run them in parallel. Before MT: digits pass through, and
        curated interjections take the table (measured 2026-08-27: EVERY
        one-word input draws NLLB's subtitle-dialog prior). After MT: the
        dialog-artifact sanitizer strips that prior's signature, and the
        detector flags on the recalibrated threshold. Every discard logs."""
        sents = [s for s in _SENT_SPLIT.split(text.strip()) if s]
        if not sents:
            return ""
        parts = [None] * len(sents)
        todo = []
        for i, sent in enumerate(sents):
            if _DIGITS_ONLY.match(sent):
                parts[i] = sent   # digits pass through, never hallucinate
                continue
            concept = interjections.match(sent, src["code"])
            if concept:
                fixed = interjections.render(concept, tgt["code"])
                if fixed:
                    parts[i] = fixed
                    self.on_log(f'MT   turn#{tid} interjection "{sent}" -> '
                                f'"{fixed}" (curated table)')
                    continue
                self.on_log(f'MT   turn#{tid} interjection "{sent}" — no '
                            f'curated {tgt["code"]} form, using MT')
            todo.append((i, sent))
        if todo:
            self._tok.src_lang = src["flores"]
            toks = [self._tok.convert_ids_to_tokens(self._tok.encode(s))
                    for _, s in todo]
            results = self._tr.translate_batch(
                toks, target_prefix=[[tgt["flores"]]] * len(toks),
                beam_size=1, return_scores=True)
            for (i, sent), tk, r in zip(todo, toks, results):
                out, ratio, per_tok, dig_ok = self._score(sent, tk, r)
                clean = _desubtitle(out)
                if clean != out:
                    self.on_log(f'MT   turn#{tid} sanitized dialog artifact: '
                                f'"{out}" -> "{clean}"')
                    out = clean
                flagged = (ratio > config.MT_RATIO_FLAG
                           or per_tok < -0.40 or not dig_ok)
                if flagged and len(sent.split()) < 4:
                    self.on_log(f"MT   turn#{tid} DISCARDED suspect fragment "
                                f'"{sent}" -> "{out}" (ratio {ratio:.2f}, '
                                f"score {per_tok:.2f}, digits_ok={dig_ok})")
                    continue
                if flagged:
                    self.on_log(f"MT   turn#{tid} FLAG (kept): "
                                f"ratio {ratio:.2f} score {per_tok:.2f} "
                                f"digits_ok={dig_ok}")
                parts[i] = out
        return " ".join(p for p in parts if p).strip()

    def _score(self, sent, toks, r):
        hyp = r.hypotheses[0][1:]
        out = self._tok.decode(self._tok.convert_tokens_to_ids(hyp),
                               skip_special_tokens=True).strip()
        ratio = len(hyp) / max(1, len(toks))
        per_tok = (r.scores[0] / max(1, len(hyp))) if r.scores else 0.0
        dig_ok = set(re.findall(r"\d", out)) <= set(re.findall(r"\d", sent))
        return out, ratio, per_tok, dig_ok

    def _translate_one(self, sent, src_f, tgt_f):
        self._tok.src_lang = src_f
        toks = self._tok.convert_ids_to_tokens(self._tok.encode(sent))
        r = self._tr.translate_batch([toks], target_prefix=[[tgt_f]],
                                     beam_size=1, return_scores=True)[0]
        return self._score(sent, toks, r)
