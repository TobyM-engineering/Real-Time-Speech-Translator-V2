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
            compute_type="int8", inter_threads=1,
            intra_threads=config.MT_THREADS)
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
                out = self._translate(text, src["flores"], tgt["flores"],
                                      turn.turn_id)
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

    def _translate(self, text, src_f, tgt_f, tid):
        parts = []
        for sent in _SENT_SPLIT.split(text.strip()):
            if not sent:
                continue
            if _DIGITS_ONLY.match(sent):
                parts.append(sent)   # digits pass through, never hallucinate
                continue
            out, ratio, per_tok, dig_ok = self._translate_one(sent, src_f, tgt_f)
            flagged = ratio > 1.25 or per_tok < -0.40 or not dig_ok
            if flagged and len(sent.split()) < 4:
                self.on_log(f"MT   turn#{tid} dropped suspect fragment "
                            f'"{sent}" -> "{out}" (ratio {ratio:.2f}, '
                            f"score {per_tok:.2f})")
                continue
            if flagged:
                self.on_log(f"MT   turn#{tid} FLAG (kept): ratio {ratio:.2f} "
                            f"score {per_tok:.2f} digits_ok={dig_ok}")
            parts.append(out)
        return " ".join(parts).strip()

    def _translate_one(self, sent, src_f, tgt_f):
        self._tok.src_lang = src_f
        toks = self._tok.convert_ids_to_tokens(self._tok.encode(sent))
        r = self._tr.translate_batch([toks], target_prefix=[[tgt_f]],
                                     beam_size=1, return_scores=True)[0]
        hyp = r.hypotheses[0][1:]
        out = self._tok.decode(self._tok.convert_tokens_to_ids(hyp),
                               skip_special_tokens=True).strip()
        ratio = len(hyp) / max(1, len(toks))
        per_tok = (r.scores[0] / max(1, len(hyp))) if r.scores else 0.0
        dig_ok = set(re.findall(r"\d", out)) <= set(re.findall(r"\d", sent))
        return out, ratio, per_tok, dig_ok
