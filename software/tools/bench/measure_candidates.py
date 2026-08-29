"""One-off measurements for the optimization ranking:
1. SenseVoice vs whisper base on the SAME English mic audio (tonight's dumps)
2. NLLB intra_threads 4 vs the pipeline's 3 (same texts as profile_es_fr)
No pipeline code touched."""
import os
import sys
import time
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from src import config

D = "/tmp/translator_dumps"
WAVS = ["asr_turn1.wav", "asr_turn3.wav", "asr_turn4.wav"]  # 1.2s, 1.4s, 10.3s


def load(path):
    w = wave.open(path, "rb")
    x = np.frombuffer(w.readframes(w.getnframes()),
                      dtype=np.int16).astype(np.float32) / 32768.0
    w.close()
    return x


print("== SenseVoice vs whisper base, same English audio ==", flush=True)
import sherpa_onnx as so
from faster_whisper import WhisperModel

sense = so.OfflineRecognizer.from_sense_voice(
    model=f"{config.MODELS}/sensevoice/model.int8.onnx",
    tokens=f"{config.MODELS}/sensevoice/tokens.txt",
    num_threads=config.ASR_THREADS, use_itn=True, language="auto")
whisper = WhisperModel(f"{config.MODELS}/whisper-base-ct2", device="cpu",
                       compute_type="int8", cpu_threads=config.ASR_THREADS)
# warm both
st = sense.create_stream()
st.accept_waveform(config.SR, np.zeros(config.SR, dtype=np.float32))
sense.decode_stream(st)
list(whisper.transcribe(np.zeros(config.SR, dtype=np.float32),
                        language="en", beam_size=1)[0])

for name in WAVS:
    audio = load(f"{D}/{name}")
    dur = len(audio) / config.SR
    tw = []
    for _ in range(2):
        t0 = time.time()
        segs, _ = whisper.transcribe(audio, language="en", beam_size=1,
                                     condition_on_previous_text=False,
                                     without_timestamps=True)
        wtxt = " ".join(s.text.strip() for s in segs).strip()
        tw.append(time.time() - t0)
    ts = []
    for _ in range(2):
        t0 = time.time()
        st = sense.create_stream()
        st.accept_waveform(config.SR, audio)
        sense.decode_stream(st)
        stxt = st.result.text.strip()
        ts.append(time.time() - t0)
    print(f"\n{name} ({dur:.1f}s audio):", flush=True)
    print(f"  whisper    {min(tw):.2f}s: \"{wtxt}\"", flush=True)
    print(f"  sensevoice {min(ts):.2f}s: \"{stxt}\"", flush=True)

print("\n== NLLB intra_threads=4 (pipeline uses 3) ==", flush=True)
import re
import ctranslate2
import transformers

_SPLIT = re.compile(r"(?<=[.!?。！？…])\s+")
SHORT = "Hello, is this thing on?"
PARA = ("Hi, is this thing on? How long will it take if I just keep talking "
        "and talking? Will it translate at all? Or do I have to let go for "
        "it to start translating?")
tok = transformers.AutoTokenizer.from_pretrained(
    f"{config.MODELS}/nllb-tokenizer", src_lang="eng_Latn")
tr4 = ctranslate2.Translator(f"{config.MODELS}/nllb-600m-int8", device="cpu",
                             compute_type="int8", inter_threads=1,
                             intra_threads=4)


def run(trx, text, tgt):
    total = 0.0
    for s in [p for p in _SPLIT.split(text.strip()) if p]:
        toks = tok.convert_ids_to_tokens(tok.encode(s))
        t0 = time.time()
        trx.translate_batch([toks], target_prefix=[[tgt]], beam_size=1)
        total += time.time() - t0
    return total


run(tr4, "Hello.", "spa_Latn")  # warm
for label, text in (("short", SHORT), ("paragraph", PARA)):
    for tgt, code in (("spa_Latn", "es"), ("fra_Latn", "fr")):
        times = [run(tr4, text, tgt) for _ in range(3)]
        print(f"MT intra4 {label:9s} -> {code}: min {min(times):5.2f}  "
              f"reps {['%.2f' % t for t in times]}", flush=True)
