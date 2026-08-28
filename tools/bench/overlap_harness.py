"""Overlap-deferral harness (2026-08-28): stream constructed STEREO audio
through the REAL AudioFrontend and verify that ambiguous-band speech during
simultaneous talk is deferred, waits for the dominant speaker, releases as a
[deferred-overlap] turn, and decodes. Scenario 2 verifies the depth cap.
Voices: real clips (A = live dump English, B = bench Spanish), cross-mixed so
the weak side lands in the ambiguous band."""
import sys
import threading
import time
import wave

import numpy as np

sys.path.insert(0, "<REPO-ROOT>")
import src.frontend as F
from src import config

SR = config.SR


def load(f):
    w = wave.open(f, "rb")
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    w.close()
    return x.astype(np.float32) / 32768.0


class FakeCap:
    stereo = None   # (N, 2) float32, set per scenario

    def __init__(self, cb):
        self.error = None
        self._cb = cb
        self._alive = True

    def start(self):
        st = np.clip(FakeCap.stereo * 32767, -32767, 32767).astype(np.int16)

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


def mix(total_s, a_events, b_events, fl_cross=0.12, fr_gain=0.9,
        fr_cross=0.28, b_amp=0.5):
    """a_events/b_events: list of (t_start, mono float array)."""
    n = int(total_s * SR)
    va, vb = np.zeros(n), np.zeros(n)
    for t, x in a_events:
        i = int(t * SR)
        va[i:i + len(x)] += x[:n - i]
    for t, x in b_events:
        i = int(t * SR)
        vb[i:i + len(x)] += x[:n - i] * b_amp
    st = np.zeros((n, 2), dtype=np.float32)
    st[:, 0] = np.clip(va + fl_cross * vb, -1, 1)
    st[:, 1] = np.clip(fr_gain * vb + fr_cross * va, -1, 1)
    return st


def run(name, stereo, timeout=30):
    print(f"\n===== {name} =====", flush=True)
    FakeCap.stereo = stereo
    segs, logs = [], []

    def on_log(m):
        logs.append(m)
        if any(k in m for k in ("SEG", "SPLIT", "DEFER")):
            print("  ", m, flush=True)

    fe = F.AudioFrontend(
        on_log=on_log,
        on_segment=lambda t, audio, ov, cont: segs.append((t, audio)))
    fe.start()
    t0 = time.time()
    while fe._cap.is_alive() and time.time() - t0 < timeout:
        time.sleep(0.1)
    time.sleep(1.5)
    fe.stop()
    return segs, logs


# COMMITTED clips only — dump files get overwritten by every live session
# (learned 2026-08-28: asr_turn1.wav silently became a 0.7 s clip and the
# blip slices came out empty). deL is the continuous "dominant" voice.
deL = load("tools/bench/clips/bench_de_long.wav")         # 7.8 s continuous
en = deL[:int(3.5 * SR)]                                  # "A utterance"
es = load("tools/bench/clips/bench_es_short.wav")         # 3.6 s Spanish

# --- scenario 1: B speaks INSIDE A's utterance; A speaks again right after,
# so B's deferred segment must WAIT for A's second utterance to finish
s1 = mix(11.0,
         a_events=[(0.0, en), (4.3, en)],
         b_events=[(0.8, es[:int(2.4 * SR)])],
         b_amp=1.3)   # lands B ~+1..+2 dB: positive-half, deferrable
segs, logs = run("scenario 1: defer, wait, release, decode", s1)

deferred = [m for m in logs if "DEFER_OVERLAP" in m]
released = [m for m in logs if "deferred-overlap" in m]
print(f"\n  deferred: {len(deferred)}  released: {len(released)}")

import sherpa_onnx as so
from faster_whisper import WhisperModel
P = f"{config.MODELS}/parakeet-tdt-v3-int8"
pk = so.OfflineRecognizer.from_transducer(
    encoder=f"{P}/encoder.int8.onnx", decoder=f"{P}/decoder.int8.onnx",
    joiner=f"{P}/joiner.int8.onnx", tokens=f"{P}/tokens.txt",
    num_threads=3, model_type="nemo_transducer")
wh = WhisperModel(f"{config.MODELS}/whisper-base-ct2", device="cpu",
                  compute_type="int8", cpu_threads=3)
for t, audio in segs:
    if t.person == config.PERSON_A:
        st = pk.create_stream()
        st.accept_waveform(SR, audio)
        pk.decode_stream(st)
        txt = st.result.text.strip()
    else:
        ss, _ = wh.transcribe(audio, language="es", beam_size=1,
                              condition_on_previous_text=False,
                              without_timestamps=True)
        txt = " ".join(s.text.strip() for s in ss).strip()
    print(f'  turn#{t.turn_id} ch={t.person} {t.t1-t.t0:.1f}s '
          f'span {t.t0:.2f}-{t.t1:.2f}: "{txt}"')

# --- scenario 2: three B blips inside one long A stretch -> depth cap drops
# the oldest (OVERLAP_DEFER_MAX = 2)
# B's blips are copies of A's CONCURRENT audio at controlled gain, so the
# cross-channel ratio is deterministic (free-running two-voice mixes ride
# the speech envelopes and dodge the ±6 band). b_amp=1.5 lands the blips
# slightly POSITIVE (~+1 dB, inside the deferrable non-negative half —
# b_amp=1.12 measured -1.0..-1.5 dB, which now correctly drops). Four
# blips against OVERLAP_DEFER_MAX=2 forces two queue-full drops.
blips = []
for t in (0.8, 2.6, 4.4, 6.2):
    pos = t % 3.5
    blips.append((t, deL[int(pos * SR):int((pos + 0.7) * SR)]))
s2 = mix(13.0,
         a_events=[(0.0, en), (3.5, en), (7.0, en)],
         b_events=blips,
         fr_cross=0.0, b_amp=1.5)
segs2, logs2 = run("scenario 2: depth cap", s2)
full = [m for m in logs2 if "queue full" in m]
rel2 = [m for m in logs2 if "deferred-overlap" in m]
print(f"\n  queue-full drops: {len(full)}  released after: {len(rel2)}")

# --- scenario 3: NEGATIVE-half ambiguity (other voice dominant on this
# mic, ratio ~ -1 dB) must DROP, never defer — the 2026-08-28 regression
# (B's bleed decoded on A and played back into her own ear)
neg = [(t, deL[int((t % 3.5) * SR):int((t % 3.5 + 0.7) * SR)])
       for t in (0.8, 2.6)]
s3 = mix(8.0,
         a_events=[(0.0, en), (3.5, en)],
         b_events=neg,
         fr_cross=0.0, b_amp=1.12)
segs3, logs3 = run("scenario 3: negative half drops", s3)
d3 = [m for m in logs3 if "DEFER_OVERLAP" in m and "ch=B" in m]
amb3 = [m for m in logs3 if "DROP_AMBIGUOUS" in m and "ch=B" in m]
print(f"\n  B deferred: {len(d3)} (expect 0)  B ambiguous-dropped: "
      f"{len(amb3)} (expect >=1)")
