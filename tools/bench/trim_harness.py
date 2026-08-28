"""Trim-path harness: stream a real dump WAV through the REAL AudioFrontend
(fake capture) with a fake playback interval covering part of the utterance,
and verify the completed GATE_TRIM path (2026-08-28): the playback-free
remainder is sliced, re-ratio-tested, accepted, and decodes to the expected
words. Prints full-clip decode vs kept decode so the trimmed-away text is
visible. Usage: trim_harness.py <wav> <cover_from_s> <cover_to_s>"""
import sys
import threading
import time
import wave

import numpy as np

sys.path.insert(0, "<REPO-ROOT>")
import src.frontend as F
from src import config


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

wav, c0, c1 = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
FakeCap.wav_path = wav
segs = []
fe = F.AudioFrontend(
    on_log=lambda m: print("  ", m, flush=True),
    on_segment=lambda t, audio, ov, cont: segs.append((t, audio)))
fe.start()
fe.ledger.add(config.PERSON_A, c0, c1)   # fake playback into A's ear
print(f"fake playback into A over stream {c0:.2f}-{c1:.2f}s", flush=True)
t0 = time.time()
while fe._cap.is_alive() and time.time() - t0 < 30:
    time.sleep(0.1)
time.sleep(1.5)
fe.stop()

import sherpa_onnx as so
P = f"{config.MODELS}/parakeet-tdt-v3-int8"
pk = so.OfflineRecognizer.from_transducer(
    encoder=f"{P}/encoder.int8.onnx", decoder=f"{P}/decoder.int8.onnx",
    joiner=f"{P}/joiner.int8.onnx", tokens=f"{P}/tokens.txt",
    num_threads=3, model_type="nemo_transducer")


def decode(a):
    st = pk.create_stream()
    st.accept_waveform(config.SR, a)
    pk.decode_stream(st)
    return st.result.text.strip()


w = wave.open(wav, "rb")
full = (np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        .astype(np.float32) / 32768.0)
w.close()
print(f'\nFULL clip decode : "{decode(full)}"')
for t, audio in segs:
    print(f'kept turn#{t.turn_id} {t.t1-t.t0:.2f}s span '
          f'{t.t0:.2f}-{t.t1:.2f}: "{decode(audio)}"')
if not segs:
    print("NO segment reached ASR — trim path FAILED")
