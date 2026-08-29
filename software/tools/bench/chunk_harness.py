"""Offline harness: stream real dump WAVs through the REAL AudioFrontend
(fake capture) and report where the mid-speech chunk closer cuts, then
whisper-decode every chunk to judge cut quality. No live audio involved."""
import os
import sys
import threading
import time
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import src.frontend as F
from src import config

D = "/tmp/translator_dumps"


class FakeCap:
    """Streams a mono WAV as FL (FR silent) in 512-frame blocks."""
    wav_path = None

    def __init__(self, cb):
        self.error = None
        self._cb = cb
        self._alive = True

    def start(self):
        w = wave.open(self.wav_path, "rb")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        w.close()
        x = np.concatenate([x, np.zeros(2 * config.SR, dtype=np.int16)])
        st = np.zeros((len(x), 2), dtype=np.int16)
        st[:, 0] = x

        def feed():
            i = 0
            while i + config.CHUNK <= len(st):
                self._cb(st[i:i + config.CHUNK].tobytes(), i)
                i += config.CHUNK
                time.sleep(0.002)
            self._alive = False
        threading.Thread(target=feed, daemon=True).start()

    def stop(self):
        self._alive = False

    def is_alive(self):
        return self._alive


F.CaptureThread = FakeCap

from faster_whisper import WhisperModel
whisper = WhisperModel(f"{config.MODELS}/whisper-base-ct2", device="cpu",
                       compute_type="int8", cpu_threads=3)
list(whisper.transcribe(np.zeros(config.SR, dtype=np.float32),
                        language="en", beam_size=1)[0])

for name in sys.argv[1:]:
    print(f"\n===== {name} =====", flush=True)
    FakeCap.wav_path = f"{D}/{name}"
    segs = []
    logs = []

    def on_log(m):
        logs.append(m)
        if "SPLIT" in m or "SEG" in m or "CHECK" in m:
            print("  ", m, flush=True)

    fe = F.AudioFrontend(
        on_log=on_log,
        on_segment=lambda t, audio, ov, cont: segs.append((t, audio, cont)))
    fe.start()
    t0 = time.time()
    while fe._cap.is_alive() and time.time() - t0 < 30:
        time.sleep(0.1)
    time.sleep(1.5)   # let the block queue drain and final segment pop
    fe.stop()

    for t, audio, cont in segs:
        txt = " ".join(s.text.strip() for s in whisper.transcribe(
            audio, language="en", beam_size=1,
            condition_on_previous_text=False, without_timestamps=True)[0]
        ).strip()
        print(f"   turn#{t.turn_id} {t.t1-t.t0:4.1f}s seam={t.forced_split} "
              f"continues={cont}: \"{txt}\"", flush=True)
