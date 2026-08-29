#!/usr/bin/env python3
"""Fetch + convert opus-mt pairs to CT2 int8 for offline use — torch-free.

Per directed pair: read the Helsinki HF README to find the OPUS bucket
directory (the linked zip filename is often stale, the directory is not),
S3-list that prefix for the newest non-eval package (preferring opus+bt),
download, convert with ct2-opus-mt-converter, install under
models/opus/{src}-{tgt}-int8 with its source.spm/target.spm. Idempotent.

Usage:
  venv/bin/python tools/fetch_opus_pairs.py en-es es-en ...
  venv/bin/python tools/fetch_opus_pairs.py --solid9
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = f"{ROOT}/models/opus"
UA = "TranslatorV2-opus-fetcher/1.0 (offline translator bench device)"
SOLID9 = ["en", "zh", "ja", "es", "ko", "pt", "it", "de", "fr", "ru"]
ALIAS = {"ja": ["ja", "jap"], "no": ["no", "nb"], "he": ["he", "heb"]}


def curl(url, out=None):
    cmd = ["curl", "-sfL", "--max-time", "600", "-A", UA, url]
    if out:
        cmd += ["-o", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout if not out else "")


def resolve_repo(a, b, pairs):
    for x in ALIAS.get(a, [a]):
        for y in ALIAS.get(b, [b]):
            tier = pairs.get(f"{x}-{y}")
            if tier == "tc-big":
                return f"Helsinki-NLP/opus-mt-tc-big-{x}-{y}"
            if tier in ("base", "base+big"):
                return f"Helsinki-NLP/opus-mt-{x}-{y}"
    return None


def newest_zip(prefix_url):
    """S3-list the bucket dir; newest non-eval zip, opus+bt preferred."""
    m = re.match(r"(https://object\.pouta\.csc\.fi/[^/]+)/(.+?)/[^/]*$",
                 prefix_url)
    if not m:
        return None
    bucket, prefix = m.group(1), m.group(2)
    ok, xml = curl(f"{bucket}/?prefix={prefix}/")
    if not ok:
        return None
    keys = [k for k in re.findall(r"<Key>([^<]+\.zip)</Key>", xml)
            if ".eval." not in k]
    if not keys:
        return None
    keys.sort(key=lambda k: ("+bt-" in k, k))   # newest, opus+bt wins
    return f"{bucket}/{keys[-1].replace('+', '%2B')}"


def _yml_vocabs(pkg):
    """Newer Tatoeba packages ship PLAIN vocab files (one token per line,
    index = line number); ct2's converter needs 'token: idx' YAML. Convert
    in place and repoint decoder.yml. (Learned on en-ko, 2026-08-27.)"""
    decp = f"{pkg}/decoder.yml"
    dec = open(decp, encoding="utf-8").read()
    for name in re.findall(r"^\s*-\s*(\S+\.vocab)\s*$", dec, re.M):
        path = f"{pkg}/{name}"
        head = open(path, encoding="utf-8").read(4000)
        if re.search(r":\s*\d+\s*$", head, re.M):
            continue                     # already token: idx format
        out = path + ".yml"
        with open(path, encoding="utf-8") as fin, \
                open(out, "w", encoding="utf-8") as fo:
            for i, line in enumerate(fin):
                tok = line.rstrip("\n\r")
                fo.write("'" + tok.replace("'", "''") + f"': {i}\n")
        dec = dec.replace(name, name + ".yml")
    open(decp, "w", encoding="utf-8").write(dec)


def convert_package(pkg, out):
    """Run the converter with the plain-vocab shim and per-side vocabulary
    padding (Marian pads embeddings past the spm size; the error names the
    side and the expected size). Returns None on success, stderr on
    failure."""
    _yml_vocabs(pkg)
    dec = open(f"{pkg}/decoder.yml", encoding="utf-8").read()
    vocabs = re.findall(r"^\s*-\s*(\S+)\s*$", dec, re.M)
    side_file = {"Source": f"{pkg}/{vocabs[0]}",
                 "Target": f"{pkg}/{vocabs[-1]}"}
    for _ in range(4):
        r = subprocess.run([f"{ROOT}/venv/bin/ct2-opus-mt-converter",
                            "--model_dir", pkg, "--output_dir", out,
                            "--quantization", "int8", "--force"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return None
        m = re.search(r"(Source|Target) vocabulary \d+ has size (\d+) but "
                      r"the model expected a vocabulary of size (\d+)",
                      r.stderr)
        if not m:
            return r.stderr
        side, have, want = m.group(1), int(m.group(2)), int(m.group(3))
        path = side_file[side]
        lines = open(path, encoding="utf-8").read().splitlines()
        if have < want:
            with open(path, "a", encoding="utf-8") as f:
                for i in range(have, want):
                    f.write(f"'<madeupword{i}>': {i}\n")
        else:
            open(path, "w", encoding="utf-8").write(
                "\n".join(lines[:want]) + "\n")
    return "vocab padding did not converge"


def fetch_pair(a, b, pairs):
    dst = f"{OUT}/{a}-{b}-int8"
    if os.path.exists(f"{dst}/model.bin") and os.path.exists(f"{dst}/source.spm"):
        print(f"{a}-{b}: already installed", flush=True)
        return True
    repo = resolve_repo(a, b, pairs)
    if repo is None:
        print(f"{a}-{b}: NO PUBLISHED MODEL — stays on NLLB", flush=True)
        return False
    ok, readme = curl(f"https://huggingface.co/{repo}/raw/main/README.md")
    urls = re.findall(r"https://object\.pouta\.csc\.fi/[^\s)\"]+\.zip", readme)
    if not ok or not urls:
        print(f"{a}-{b}: README/bucket URL not found ({repo})", flush=True)
        return False
    zip_url = newest_zip(urls[0]) or urls[0]
    tmp = tempfile.mkdtemp(prefix=f"opus_{a}{b}_")
    try:
        t0 = time.time()
        ok, _ = curl(zip_url, out=f"{tmp}/pkg.zip")
        if not ok or os.path.getsize(f"{tmp}/pkg.zip") < 1_000_000:
            print(f"{a}-{b}: download failed ({zip_url})", flush=True)
            return False
        subprocess.run(["unzip", "-o", "-q", f"{tmp}/pkg.zip", "-d",
                        f"{tmp}/pkg"], check=True)
        r = convert_package(f"{tmp}/pkg", f"{tmp}/ct2")
        if r is not None:
            print(f"{a}-{b}: CONVERT FAILED: {r.strip()[-300:]}", flush=True)
            return False
        os.makedirs(OUT, exist_ok=True)
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.move(f"{tmp}/ct2", dst)
        for f in ("source.spm", "target.spm"):
            shutil.copy(f"{tmp}/pkg/{f}", f"{dst}/{f}")
        mb = sum(os.path.getsize(f"{dst}/{f}") for f in os.listdir(dst)) / 1e6
        print(f"{a}-{b}: installed {mb:.0f} MB in {time.time()-t0:.0f}s "
              f"({os.path.basename(zip_url)})", flush=True)
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    pairs = json.load(open(f"{ROOT}/tools/opus_pairs.json"))
    if "--solid9" in sys.argv:
        wanted = [(a, b) for a in SOLID9 for b in SOLID9
                  if a != b and resolve_repo(a, b, pairs)]
        print(f"solid-9 covered set: {len(wanted)} directed pairs", flush=True)
    else:
        wanted = [tuple(p.split("-")) for p in sys.argv[1:]
                  if re.match(r"^[a-z]{2,3}-[a-z]{2,3}$", p)]
    done = 0
    for a, b in wanted:
        if fetch_pair(a, b, pairs):
            done += 1
    print(f"\n{done}/{len(wanted)} pairs installed under {OUT}", flush=True)


if __name__ == "__main__":
    main()
