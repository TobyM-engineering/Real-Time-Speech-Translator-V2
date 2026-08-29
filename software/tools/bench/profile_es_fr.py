"""One-off: profile MT and TTS for es vs fr on identical English input,
using exactly the pipeline's invocations (mt_worker/tts_worker)."""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import numpy as np
import re

from src import config

_SENT_SPLIT = re.compile(r"(?<=[.!?。！？…])\s+")

SHORT = "Hello, is this thing on?"
PARA = ("Hi, is this thing on? How long will it take if I just keep talking "
        "and talking? Will it translate at all? Or do I have to let go for "
        "it to start translating?")

print("== loading NLLB (as the pipeline does) ==", flush=True)
import ctranslate2
import transformers
t0 = time.time()
tok = transformers.AutoTokenizer.from_pretrained(
    f"{config.MODELS}/nllb-tokenizer", src_lang="eng_Latn")
tr = ctranslate2.Translator(f"{config.MODELS}/nllb-600m-int8", device="cpu",
                            compute_type="int8", inter_threads=1,
                            intra_threads=config.MT_THREADS)


def translate_one(sent, tgt):
    toks = tok.convert_ids_to_tokens(tok.encode(sent))
    r = tr.translate_batch([toks], target_prefix=[[tgt]],
                           beam_size=1, return_scores=True)[0]
    hyp = r.hypotheses[0][1:]
    out = tok.decode(tok.convert_tokens_to_ids(hyp),
                     skip_special_tokens=True).strip()
    return out, len(toks), len(hyp)


translate_one("Hello.", "spa_Latn")  # warm
print(f"   loaded+warm in {time.time()-t0:.1f}s", flush=True)

results = {}
for label, text in (("short", SHORT), ("paragraph", PARA)):
    sents = [s for s in _SENT_SPLIT.split(text.strip()) if s]
    for tgt, code in (("spa_Latn", "es"), ("fra_Latn", "fr")):
        times = []
        detail = []
        for rep in range(3):
            t1 = time.time()
            outs = []
            per_sent = []
            for s in sents:
                ts = time.time()
                out, ti, to = translate_one(s, tgt)
                per_sent.append((time.time() - ts, ti, to))
                outs.append(out)
            times.append(time.time() - t1)
            if rep == 0:
                detail = per_sent
                joined = " ".join(outs)
        results[(label, code, "mt")] = (min(times), sorted(times)[1], joined)
        ps = "  ".join(f"{d:.2f}s({ti}->{to}tok)" for d, ti, to in detail)
        print(f"MT {label:9s} -> {code}: min {min(times):5.2f}  "
              f"med {sorted(times)[1]:5.2f}  reps {['%.2f' % t for t in times]}",
              flush=True)
        print(f"   per-sentence: {ps}", flush=True)

print("\n== Piper voices (as the pipeline loads them) ==", flush=True)
from piper import PiperVoice
for code, vfile in (("es", "es_ES-davefx-medium"), ("fr", "fr_FR-siwis-medium")):
    t0 = time.time()
    v = PiperVoice.load(f"{config.MODELS}/piper/{vfile}.onnx")
    load_s = time.time() - t0
    text = results[("paragraph", code, "mt")][2]
    synth = []
    for rep in range(2):
        t1 = time.time()
        parts = [c.audio_int16_array for c in v.synthesize(text)]
        x = np.concatenate(parts)
        synth.append(time.time() - t1)
    dur = len(x) / v.config.sample_rate
    print(f"TTS {code}: voice load {load_s:.2f}s | synth paragraph "
          f"{synth[0]:.2f}s then {synth[1]:.2f}s for {dur:.1f}s audio "
          f"(warm RTF {synth[1]/dur:.2f})", flush=True)

print("\nMT outputs (paragraph):", flush=True)
for code in ("es", "fr"):
    print(f"  {code}: {results[('paragraph', code, 'mt')][2]}", flush=True)
