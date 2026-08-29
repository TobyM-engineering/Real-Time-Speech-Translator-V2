#!/usr/bin/env python3
"""Download every Piper voice named in src/ui/languages.json that isn't already on
disk (or is size-mismatched). Idempotent. Paths and sizes come from the
piper-voices repo index.

Usage: venv/bin/python tools/fetch_all_voices.py <piper_voices.json>
"""
import os
import json
import pathlib
import subprocess
import sys

M = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/models/piper")
M.mkdir(parents=True, exist_ok=True)
catalog = json.load(open(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/src/src/ui/languages.json"))
index = json.load(open(sys.argv[1]))

need = [e["ttsVoice"] for e in catalog if e["tts"] == "piper"]
fail, got, skipped = [], 0, 0
for key in need:
    for path, meta in index[key]["files"].items():
        if not (path.endswith(".onnx") or path.endswith(".onnx.json")):
            continue
        dest = M / pathlib.Path(path).name
        if dest.exists() and dest.stat().st_size == meta.get("size_bytes", -1):
            skipped += 1
            continue
        url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{path}"
        r = subprocess.run(["curl", "-fL", "--retry", "3", "-o", str(dest), url],
                           capture_output=True)
        if r.returncode:
            fail.append(path)
            print(f"FAIL {path}", flush=True)
        else:
            got += 1
            print(f"ok   {dest.name} ({dest.stat().st_size/1e6:.0f} MB)", flush=True)

print(f"done: {got} downloaded, {skipped} already present, {len(fail)} failed")
sys.exit(1 if fail else 0)
