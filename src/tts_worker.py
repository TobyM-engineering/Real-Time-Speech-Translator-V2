"""T4 TTS worker: Piper voices (catalog-selected, LRU of 3 resident) plus
Supertonic-3 for its exclusive languages (hr/lt). Output resampled to the
mixer rate. Voice loads (~1.9 s measured) happen here, never on the turn path
for already-resident voices."""
import queue
import threading
import time

import numpy as np

from src import config


class TtsWorker(threading.Thread):
    def __init__(self, on_log, on_synth, preload_codes, by_code,
                 on_ready=None, active_codes=None):
        """on_ready: called once after the startup preloads finish (ready is
        gated on it). active_codes: () -> set of currently selected language
        codes — the LRU never evicts one of these."""
        super().__init__(name="tts", daemon=True)
        self.on_log = on_log
        self.on_synth = on_synth
        self.preload = preload_codes
        self.by_code = by_code
        self.on_ready = on_ready
        self.active_codes = active_codes
        self.q = queue.Queue()
        self._voices = {}        # code -> (kind, engine, sr), LRU order
        self._supertonic = None
        self._stopping = threading.Event()

    def submit(self, turn, text, tgt_entry, ear):
        self.q.put((turn, text, tgt_entry, ear))

    def preload_voice(self, entry):
        """Picker-time load: runs on this worker's thread, so capture, ASR,
        MT, and the UI never stall. At worst one queued synth for the other
        ear waits ~2 s behind it, once, at switch time."""
        self.q.put((None, None, entry, None))

    def stop(self):
        self._stopping.set()

    def run(self):
        try:
            for code in self.preload:
                self._get_voice(self.by_code[code])
            self.on_log(f"TTS  voices ready: {list(self._voices)}")
        except Exception as e:
            self.on_log(f"TTS  startup preload FAILED: {e} — affected turns "
                        f"will fail loudly; the other direction still works")
        if self.on_ready:
            self.on_ready()
        while not self._stopping.is_set():
            try:
                turn, text, tgt, ear = self.q.get(timeout=0.25)
            except queue.Empty:
                continue
            if turn is None:   # picker-time preload sentinel, no synth
                try:
                    self._get_voice(tgt)
                except Exception as e:
                    self.on_log(f"TTS  preload of {tgt.get('code')} "
                                f"FAILED: {e}")
                continue
            if turn.cancelled:
                self.on_log(f"TTS  turn#{turn.turn_id} cancelled — skipped")
                continue
            t0 = time.time()
            try:
                samples = self._synth(tgt, text)
            except Exception as e:
                turn.state = "tts_failed"   # terminal: keeps the lag metric honest
                self.on_log(f"TTS  turn#{turn.turn_id} FAILED: {e}")
                continue
            if turn.cancelled:
                self.on_log(f"TTS  turn#{turn.turn_id} cancelled during synth")
                continue
            dur = len(samples) / config.OUT_RATE
            self.on_log(f"TTS  turn#{turn.turn_id} {dur:.1f}s audio "
                        f"({time.time()-t0:.2f}s synth) -> ear {ear}")
            turn.state = "synthesized"
            self.on_synth(turn, ear, samples)

    # ------------------------------------------------------------------
    def _get_voice(self, entry):
        code = entry["code"]
        if code in self._voices:
            v = self._voices.pop(code)
            self._voices[code] = v          # LRU refresh
            return v
        t0 = time.time()
        if entry["tts"] == "piper":
            from piper import PiperVoice
            v = PiperVoice.load(
                f"{config.MODELS}/piper/{entry['ttsVoice']}.onnx")
            item = ("piper", v, v.config.sample_rate)
        else:
            item = ("supertonic", self._get_supertonic(), 44100)
        self._voices[code] = item
        if len(self._voices) > 3:
            # never evict a currently selected voice — that would put the
            # cold-load stall right back on the next turn
            active = self.active_codes() if self.active_codes else set()
            old = next((k for k in self._voices
                        if k not in active and k != code), None)
            if old is None:
                old = next(iter(self._voices))
            del self._voices[old]
            self.on_log(f"TTS  evicted voice {old}")
        self.on_log(f"TTS  loaded voice {code} in {time.time()-t0:.1f}s")
        return item

    def _get_supertonic(self):
        if self._supertonic is None:
            import sherpa_onnx as so
            S = f"{config.MODELS}/supertonic-3-int8"
            cfg = so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(
                supertonic=so.OfflineTtsSupertonicModelConfig(
                    text_encoder=f"{S}/text_encoder.int8.onnx",
                    duration_predictor=f"{S}/duration_predictor.int8.onnx",
                    vector_estimator=f"{S}/vector_estimator.int8.onnx",
                    vocoder=f"{S}/vocoder.int8.onnx",
                    voice_style=f"{S}/voice.bin",
                    unicode_indexer=f"{S}/unicode_indexer.bin",
                    tts_json=f"{S}/tts.json"),
                num_threads=config.ASR_THREADS))
            self._supertonic = so.OfflineTts(cfg)
        return self._supertonic

    def _synth(self, entry, text):
        kind, engine, sr = self._get_voice(entry)
        if kind == "piper":
            parts = [c.audio_int16_array for c in engine.synthesize(text)]
            x = (np.concatenate(parts).astype(np.float32) / 32768.0
                 if parts else np.zeros(1, dtype=np.float32))
        else:
            audio = engine.generate(text, sid=0, speed=1.0)
            x = np.asarray(audio.samples, dtype=np.float32)
            sr = audio.sample_rate
        if sr != config.OUT_RATE:
            n = np.arange(0, len(x), sr / config.OUT_RATE)
            x = np.interp(n, np.arange(len(x)), x).astype(np.float32)
        return x
