"""T2 ASR worker (design doc): owns whisper + SenseVoice, consumes accepted
segments, applies the fragment merge window, emits transcripts.

Merge window (measured policy): a segment under MERGE_SHORT_SEG seconds or
MERGE_SHORT_WORDS words is held MERGE_HOLD seconds for a continuation —
MERGE_HOLD_DANGLING if its text ends in a dangling function word. A
continuation arriving within MERGE_MAX_GAP of the held segment's end is
concatenated and re-decoded as one utterance; the later turn is marked merged.
"""
import os
import queue
import re
import threading
import time
import wave

import numpy as np

from src import config, interjections

_DUMP_DIR = os.environ.get("TXV2_DUMP_DIR")
_DUMP_MAX = 8

_CJK = {"zh", "ja", "ko"}
_NONWORD = re.compile(r"[\W_]+", re.UNICODE)


def _normtokens(text, cjk):
    t = _NONWORD.sub(" ", text.casefold()).strip()
    return list(t.replace(" ", "")) if cjk else t.split()


class AsrWorker(threading.Thread):
    def __init__(self, registry, get_lang, on_log, on_transcript, on_ready,
                 on_dropped):
        """get_lang(person) -> catalog entry (dict with code/asr/...).
        Exactly one terminal callback fires per submitted turn:
        on_transcript(turn, text) or on_dropped(turn, reason) — the pending
        counter upstream depends on this. All callbacks run on this thread."""
        super().__init__(name="asr", daemon=True)
        self.registry = registry
        self.get_lang = get_lang
        self.on_log = on_log
        self.on_transcript = on_transcript
        self.on_ready = on_ready
        self.on_dropped = on_dropped
        self.q = queue.Queue()          # unbounded by design (D5: never drop)
        self.current_load = None        # (person, seconds) while decoding
        self._stopping = threading.Event()

    def submit(self, turn, audio):
        self.q.put((turn, audio))

    def backlog_seconds(self):
        return sum(len(a) for _, a in list(self.q.queue)) / config.SR

    def backlog_for(self, person):
        """Seconds of this person's captured audio not yet transcribed —
        the D5 pause-request metric. Lock-free reads; approximate is fine."""
        s = sum(len(a) / config.SR for t, a in list(self.q.queue)
                if t.person == person)
        cur = self.current_load
        if cur and cur[0] == person:
            s += cur[1]
        return s

    def stop(self):
        self._stopping.set()

    # ------------------------------------------------------------------
    def run(self):
        t0 = time.time()
        import sherpa_onnx as so
        from faster_whisper import WhisperModel
        self._whisper = WhisperModel(f"{config.MODELS}/whisper-base-ct2",
                                     device="cpu", compute_type="int8",
                                     cpu_threads=config.ASR_THREADS)
        self._sense = so.OfflineRecognizer.from_sense_voice(
            model=f"{config.MODELS}/sensevoice/model.int8.onnx",
            tokens=f"{config.MODELS}/sensevoice/tokens.txt",
            num_threads=config.ASR_THREADS, use_itn=True, language="auto")
        # Parakeet TDT 0.6B v3 int8 — the 25 European languages (multilingual,
        # auto language, full punctuation). 641 MB read from SD dominates a
        # cold start; the READY gate deliberately waits for it.
        P = f"{config.MODELS}/parakeet-tdt-v3-int8"
        self._parakeet = so.OfflineRecognizer.from_transducer(
            encoder=f"{P}/encoder.int8.onnx", decoder=f"{P}/decoder.int8.onnx",
            joiner=f"{P}/joiner.int8.onnx", tokens=f"{P}/tokens.txt",
            num_threads=config.ASR_THREADS, model_type="nemo_transducer")
        # warm all three so the first real turn pays no first-call cost
        list(self._whisper.transcribe(np.zeros(config.SR, dtype=np.float32),
                                      language="en", beam_size=1)[0])
        for eng in (self._sense, self._parakeet):
            st = eng.create_stream()
            st.accept_waveform(config.SR, np.zeros(config.SR, dtype=np.float32))
            eng.decode_stream(st)
        self.on_log(f"ASR  models loaded and warm in {time.time()-t0:.1f}s "
                    f"(whisper + sensevoice + parakeet)")
        self.on_ready()

        held = {}   # person -> dict(turn, audio, text, deadline)
        while not self._stopping.is_set():
            timeout = 0.25
            if held:
                timeout = max(0.02, min(h["deadline"] for h in held.values())
                              - time.time())
            try:
                item = self.q.get(timeout=timeout)
            except queue.Empty:
                item = None

            now = time.time()
            if item:
                turn, audio = item
                if turn.cancelled:
                    self.on_dropped(turn, "cancelled before decode")
                else:
                    self._process_segment(turn, audio, now, held)
            # release any holds whose window closed
            for person in list(held):
                if now >= held[person]["deadline"]:
                    h = held.pop(person)
                    self._release(h["turn"], h["text"], "hold expired")

    # ------------------------------------------------------------------
    def _process_segment(self, turn, audio, now, held):
        person = turn.person
        h = held.pop(person, None)
        if h is not None:
            gap = turn.t0 - h["turn"].t1
            if gap <= config.MERGE_MAX_GAP and not h["turn"].cancelled:
                base = h["turn"]
                self.on_log(f"ASR  merge turn#{turn.turn_id} into "
                            f"turn#{base.turn_id} (gap {gap:.2f}s)")
                turn.state = "merged"
                base.forced_split = turn.forced_split  # seam status of last piece
                self.on_dropped(turn, f"merged into #{base.turn_id}")
                audio = np.concatenate([h["audio"], audio])
                turn = base
                turn.t1 = turn.t0 + len(audio) / config.SR
            else:
                self._release(h["turn"], h["text"], f"gap {gap:.2f}s too long")

        entry = self.get_lang(person)
        if _DUMP_DIR and getattr(self, "_dumped", 0) < _DUMP_MAX:
            self._dumped = getattr(self, "_dumped", 0) + 1
            path = f"{_DUMP_DIR}/asr_turn{turn.turn_id}.wav"
            w = wave.open(path, "wb")
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(config.SR)
            w.writeframes((np.clip(audio, -1, 1) * 32767)
                          .astype(np.int16).tobytes())
            w.close()
            span = turn.t1 - turn.t0
            self.on_log(f"DUMP turn#{turn.turn_id} -> {path} | "
                        f"{len(audio)} samples @{config.SR} = "
                        f"{len(audio)/config.SR:.2f}s | capture span "
                        f"{span:.2f}s | mono | engine={entry['asr']} "
                        f"lang={entry['code']}")
        t0 = time.time()
        self.current_load = (person, len(audio) / config.SR)
        text = self._decode(entry, audio)
        self.current_load = None
        dt = time.time() - t0
        if not text:
            turn.state = "empty"
            self.on_dropped(turn, f"empty transcript "
                            f"({len(audio)/config.SR:.1f}s audio)")
            return
        if turn.cancelled:
            self.on_dropped(turn, "cancelled during decode")
            return
        if self._nonspeech_reject(entry, audio, text, turn):
            return

        dur = len(audio) / config.SR
        n_units = len(text) if entry["code"] in _CJK else len(text.split())
        short = dur < config.MERGE_SHORT_SEG or n_units < config.MERGE_SHORT_WORDS
        last = text.rstrip(".,;: …").split()[-1].lower() if text.split() else ""
        dangling = entry["code"] not in _CJK and last in config.DANGLING_WORDS
        seam = turn.forced_split
        if dur >= config.MERGE_MAX_TOTAL:
            hold_s = 0.0   # chain cap: never grow past the whisper window
        elif dangling:
            hold_s = config.MERGE_HOLD_DANGLING
        elif short or seam:
            hold_s = config.MERGE_HOLD
        else:
            hold_s = 0.0
        self.on_log(f'ASR  turn#{turn.turn_id} {dur:.1f}s -> "{text}" '
                    f"({dt:.2f}s decode)")
        if hold_s > 0.0:
            held[person] = {"turn": turn, "audio": audio, "text": text,
                            "deadline": now + hold_s}
            why = ("dangling ending" if dangling
                   else "forced-split seam" if seam else "short segment")
            self.on_log(f"ASR  turn#{turn.turn_id} held {hold_s:.1f}s "
                        f"for merge ({why})")
        else:
            self._release(turn, text, None)

    def _nonspeech_reject(self, entry, audio, text, turn):
        """Tier-1 non-speech floor: very short, few-word segments get a free
        SenseVoice cross-decode. A language flip (noise reads as ja/yue) or
        zero token overlap means the primary engine invented words from a
        non-speech sound — drop silently (no gap tone: nothing was said),
        log loudly. Only where SenseVoice knows the source language, and
        never when SenseVoice IS the primary engine."""
        dur = len(audio) / config.SR
        if (dur >= config.NONSPEECH_MAX_S
                or entry["asr"] == "sensevoice"
                or entry["code"] not in ("en", "zh", "ja", "ko", "yue")):
            return False
        cjk = entry["code"] in _CJK
        units = len(text) if cjk else len(text.split())
        if units > config.NONSPEECH_MAX_WORDS:
            return False
        st = self._sense.create_stream()
        st.accept_waveform(config.SR, audio)
        self._sense.decode_stream(st)
        sv_text = st.result.text.strip()
        sv_lang = st.result.lang.strip("<|>")
        flip = sv_lang != entry["code"]
        # prefix counts as agreement: SenseVoice truncates legit short words
        # ("Yeah." -> "Y.") while noise yields DIFFERENT words (Blob/Blurp)
        ta = _normtokens(text, cjk)
        tb = _normtokens(sv_text, cjk)
        agree = any(a == b or a.startswith(b) or b.startswith(a)
                    for a in ta for b in tb)
        # semantic agreement through the interjection table: the engines can
        # word the same concept differently ("Yeah." vs "Yes.", はい vs うん)
        if not agree:
            c_pk = interjections.match(text, entry["code"])
            c_sv = interjections.match(sv_text, entry["code"]) or (
                interjections.match(sv_text, sv_lang)
                if sv_lang in interjections.RECOGNIZE else None)
            agree = c_pk is not None and c_pk == c_sv
        # disagreement alone decides: every measured noise specimen fails
        # both tests anyway, and SenseVoice's language tag misfires on legit
        # clean shorts (synth "Yeah." tagged non-en) — the flip is logged as
        # evidence, not used as a trigger
        if agree:
            return False
        turn.state = "rejected_nonspeech"
        self.on_log(f'ASR  turn#{turn.turn_id} REJECTED non-speech floor '
                    f'({dur:.1f}s): pk="{text}" sv="{sv_text}" '
                    f'lang={sv_lang} '
                    f'{"lang-flip" if flip else "no-overlap"}')
        self.on_dropped(turn, "non-speech floor")
        return True

    def _release(self, turn, text, why):
        if turn.cancelled:
            self.on_dropped(turn, "cancelled while held")
            return
        if why:
            self.on_log(f"ASR  turn#{turn.turn_id} released ({why})")
        turn.state = "transcribed"
        self.on_transcript(turn, text)

    def _decode(self, entry, audio):
        if entry["asr"] == "sensevoice":
            st = self._sense.create_stream()
            st.accept_waveform(config.SR, audio)
            self._sense.decode_stream(st)
            return st.result.text.strip()
        if entry["asr"] == "parakeet":
            st = self._parakeet.create_stream()
            st.accept_waveform(config.SR, audio)
            self._parakeet.decode_stream(st)
            return st.result.text.strip()
        segs, _ = self._whisper.transcribe(
            audio, language=entry["code"], beam_size=1,
            condition_on_previous_text=False, without_timestamps=True)
        return " ".join(s.text.strip() for s in segs).strip()
