"""Parakeet TDT 0.6B v3 int8 (sherpa-onnx) vs whisper base int8:
load time, resident RAM, RTF per clip, WER vs known ground truth."""
import json
import re
import sys
import time
import wave

import numpy as np

sys.path.insert(0, "<REPO-ROOT>")
from src import config

S = ("/tmp/claude-1000/-home-<USER>-translator/"
     "234eaa0c-1c91-4b1d-a0df-178bfc6efcd6/scratchpad")
P = "<REPO-ROOT>/models/parakeet-tdt-v3-int8"
meta = json.load(open(f"{S}/bench_meta.json"))


def rss_mb():
    return int(open("/proc/self/status").read()
               .split("VmRSS:")[1].split()[0]) / 1024


def load_wav(path):
    w = wave.open(path, "rb")
    sr, ch = w.getframerate(), w.getnchannels()
    x = np.frombuffer(w.readframes(w.getnframes()),
                      dtype=np.int16).astype(np.float32) / 32768.0
    w.close()
    if ch == 2:
        x = x.reshape(-1, 2).mean(axis=1)
    if sr != 16000:
        n = np.arange(0, len(x), sr / 16000.0)
        x = np.interp(n, np.arange(len(x)), x)
    return x


_PUNCT = re.compile(r"[.,;:!?¿¡«»\"“”‘’'()\-…]+")


def norm(t):
    return _PUNCT.sub(" ", t.casefold()).split()


def wer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    d = np.zeros((len(r) + 1, len(h) + 1), dtype=np.int32)
    d[:, 0] = np.arange(len(r) + 1)
    d[0, :] = np.arange(len(h) + 1)
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i, j] = min(d[i-1, j] + 1, d[i, j-1] + 1,
                          d[i-1, j-1] + (r[i-1] != h[j-1]))
    return 100.0 * d[len(r), len(h)] / max(1, len(r))


clips = []   # (name, lang, audio, ref-or-None)
for name, m in meta.items():
    clips.append((name, m["lang"], load_wav(f"{S}/{name}"), m["ref"]))
for lang in ("es", "fr", "de", "en"):
    clips.append((f"native_{lang}.wav", lang,
                  load_wav(f"{P}/test_wavs/{lang}.wav"), None))

print(f"baseline RSS {rss_mb():.0f} MB", flush=True)

# ---- whisper base int8 (as the pipeline runs it) ----
from faster_whisper import WhisperModel
r0 = rss_mb(); t0 = time.time()
whisper = WhisperModel(f"{config.MODELS}/whisper-base-ct2", device="cpu",
                       compute_type="int8", cpu_threads=config.ASR_THREADS)
list(whisper.transcribe(np.zeros(16000, dtype=np.float32),
                        language="es", beam_size=1)[0])
print(f"whisper load+warm {time.time()-t0:.1f}s, RSS +{rss_mb()-r0:.0f} MB",
      flush=True)


def asr_whisper(a, lang):
    segs, _ = whisper.transcribe(a, language=lang, beam_size=1,
                                 condition_on_previous_text=False,
                                 without_timestamps=True)
    return " ".join(s.text.strip() for s in segs).strip()


wres = {}
for name, lang, a, ref in clips:
    ts = []
    for _ in range(2):
        t0 = time.time(); txt = asr_whisper(a, lang); ts.append(time.time()-t0)
    wres[name] = (min(ts), txt)
print(f"whisper after decodes RSS {rss_mb():.0f} MB", flush=True)

# ---- parakeet tdt 0.6b v3 int8 via sherpa-onnx ----
import sherpa_onnx as so
r0 = rss_mb(); t0 = time.time()
pk = so.OfflineRecognizer.from_transducer(
    encoder=f"{P}/encoder.int8.onnx", decoder=f"{P}/decoder.int8.onnx",
    joiner=f"{P}/joiner.int8.onnx", tokens=f"{P}/tokens.txt",
    num_threads=config.ASR_THREADS, model_type="nemo_transducer")
load_s = time.time() - t0
r1 = rss_mb()
st = pk.create_stream()
st.accept_waveform(16000, np.zeros(16000, dtype=np.float32))
pk.decode_stream(st)
print(f"parakeet load {load_s:.1f}s (+warm {time.time()-t0-load_s:.1f}s), "
      f"RSS +{r1-r0:.0f} MB load, {rss_mb():.0f} MB total after warm",
      flush=True)


def asr_pk(a):
    st = pk.create_stream()
    st.accept_waveform(16000, a)
    pk.decode_stream(st)
    return st.result.text.strip()


pres = {}
for name, lang, a, ref in clips:
    ts = []
    for _ in range(2):
        t0 = time.time(); txt = asr_pk(a); ts.append(time.time()-t0)
    pres[name] = (min(ts), txt)
print(f"parakeet after decodes RSS {rss_mb():.0f} MB\n", flush=True)

print("=== side by side ===", flush=True)
for name, lang, a, ref in clips:
    dur = len(a) / 16000.0
    wt, wtxt = wres[name]
    pt, ptxt = pres[name]
    ww = f" WER {wer(ref, wtxt):4.1f}%" if ref else ""
    pw = f" WER {wer(ref, ptxt):4.1f}%" if ref else ""
    print(f"\n{name} ({lang}, {dur:.1f}s):", flush=True)
    print(f"  whisper  {wt:5.2f}s RTF {wt/dur:.3f}{ww}: \"{wtxt}\"", flush=True)
    print(f"  parakeet {pt:5.2f}s RTF {pt/dur:.3f}{pw}: \"{ptxt}\"", flush=True)
    if ref:
        print(f"  ref: \"{ref}\"", flush=True)
