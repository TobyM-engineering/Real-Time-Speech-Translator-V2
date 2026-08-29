#!/usr/bin/env python3
"""Pre-translate the device's UI phrases into every catalog language, at build
time, using the on-device NLLB — the runtime never translates UI strings (D5).

Writes each language's strings into src/ui/languages.json under "ui", plus a
"ui_flags" list naming any output the fragment-style hallucination detector
found suspect (looser thresholds than the pipeline's: short UI strings expand
legitimately). Flagged strings are for human review, not automatic rejection.

Quality over speed: beam_size=4 — this runs once per build, latency irrelevant.
Usage: venv/bin/python tools/build_ui_strings.py
"""
import os
import json
import time

import ctranslate2
import transformers

STRINGS = {
    "ready": "Ready",
    "listening": "Listening",
    "translating": "Translating",
    "speaking": "Speaking",
    "muted": "Muted",
    "cancelled": "Cancelled",
    "pause_soft": "Please pause for a moment.",
    "pause_hard": "Please wait. I am far behind.",
    "one_at_a_time": "One at a time, please.",
    "loading_voice": "Loading voice...",
}
M = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/models"
CAT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/src/src/ui/languages.json"

tok = transformers.AutoTokenizer.from_pretrained(M + "/nllb-tokenizer",
                                                 src_lang="eng_Latn")
tr = ctranslate2.Translator(M + "/nllb-600m-int8", device="cpu",
                            compute_type="int8", inter_threads=1, intra_threads=3)
catalog = json.load(open(CAT))
t0 = time.time()
suspects = []
for e in catalog:
    if e["code"] == "en":
        e["ui"] = dict(STRINGS)
        e["ui_flags"] = []
        continue
    ui, flags = {}, []
    for key, src in STRINGS.items():
        toks = tok.convert_ids_to_tokens(tok.encode(src))
        r = tr.translate_batch([toks], target_prefix=[[e["flores"]]],
                               beam_size=4, return_scores=True)[0]
        hyp = r.hypotheses[0][1:]
        out = tok.decode(tok.convert_tokens_to_ids(hyp),
                         skip_special_tokens=True).strip()
        ratio = len(hyp) / len(toks)
        per_tok = r.scores[0] / max(1, len(hyp))
        ui[key] = out
        if ratio > 2.0 or per_tok < -0.60:
            flags.append(key)
            suspects.append(f"{e['code']}.{key}: {out!r} (ratio {ratio:.1f}, "
                            f"score {per_tok:.2f})")
    e["ui"] = ui
    e["ui_flags"] = flags
    print(f"{e['code']}: done, {len(flags)} flagged", flush=True)

json.dump(catalog, open(CAT, "w"), ensure_ascii=False, indent=1)
print(f"\nfinished in {time.time()-t0:.0f} s; {len(suspects)} suspect strings "
      f"for human review:")
for s in suspects:
    print("  ", s)
