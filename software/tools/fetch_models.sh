#!/bin/bash
# Translator V2 — fetch all offline models + convert NLLB to CTranslate2 int8.
# Idempotent: safe to re-run; skips files that already exist where cheap to check.
# Everything lands under models/, full log at models/download.log.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VENV=$ROOT/venv
M=$ROOT/models
TMP=$M/tmp
mkdir -p "$M" "$TMP" "$M/piper"
exec >>"$M/download.log" 2>&1

FAIL=""
HF=$VENV/bin/hf
[ -x "$HF" ] || HF=$VENV/bin/huggingface-cli
step()      { echo; echo "=== $1  [$(date '+%H:%M:%S')]"; }
mark_fail() { FAIL="$FAIL $1"; echo "!!! FAILED: $1"; }

echo "########## model fetch started $(date) ##########"

step "1/6 faster-whisper base, CTranslate2 format (~150 MB)"
"$HF" download Systran/faster-whisper-base --local-dir "$M/whisper-base-ct2" >/dev/null \
  || mark_fail whisper-base

step "2/6 SenseVoice small int8 via sherpa-onnx (~250 MB)"
SV=sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17
if [ -d "$M/sensevoice" ]; then
  echo "already present, skipping"
elif curl -fL --retry 3 -o "$TMP/sv.tar.bz2" \
      "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$SV.tar.bz2" \
    && tar xjf "$TMP/sv.tar.bz2" -C "$M"; then
  mv "$M/$SV" "$M/sensevoice"
  rm -f "$TMP/sv.tar.bz2"
else
  mark_fail sensevoice
fi

step "3/6 Silero VAD (onnx, ~2 MB)"
[ -s "$M/silero_vad.onnx" ] || curl -fL --retry 3 -o "$M/silero_vad.onnx" \
  "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx" \
  || mark_fail silero-vad

step "4/6 Piper voices, medium (7 languages, ~65 MB each)"
for spec in en/en_US/lessac/medium es/es_ES/davefx/medium fr/fr_FR/siwis/medium \
            de/de_DE/thorsten/medium pt/pt_BR/faber/medium ru/ru_RU/irina/medium \
            zh/zh_CN/huayan/medium; do
  IFS=/ read -r _l1 l2 name q <<<"$spec"
  base="${l2}-${name}-${q}"
  for ext in onnx onnx.json; do
    [ -s "$M/piper/$base.$ext" ] && continue
    curl -fL --retry 3 -o "$M/piper/$base.$ext" \
      "https://huggingface.co/rhasspy/piper-voices/resolve/main/$spec/$base.$ext" \
      || mark_fail "piper-$base.$ext"
  done
done

step "5/6 Supertonic Japanese TTS (search sherpa-onnx tts-models release assets)"
if ls -d "$M"/*supertonic* >/dev/null 2>&1; then
  echo "already present, skipping"
else
  AURL=$(curl -fsL "https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/tags/tts-models" \
    | "$VENV/bin/python" -c 'import json,sys; print(json.load(sys.stdin)["assets_url"])')
  MATCHES=$(for p in 1 2 3 4 5 6 7 8; do
      curl -fsL "$AURL?per_page=100&page=$p" \
        | "$VENV/bin/python" -c 'import json,sys; [print(a["browser_download_url"]) for a in json.load(sys.stdin)]'
    done | grep -i supertonic | sort -u)
  echo "supertonic assets in release:"; echo "${MATCHES:-none}"
  # Supertonic-3 is multilingual (covers Japanese); asset name carries no "ja" token.
  JA=$(echo "$MATCHES" | grep -E 'supertonic-3' | head -1)
  [ -n "$JA" ] || JA=$(echo "$MATCHES" | head -1)
  if [ -n "$JA" ]; then
    f=$(basename "$JA")
    if curl -fL --retry 3 -o "$TMP/$f" "$JA"; then
      case "$f" in
        *.tar.bz2) tar xjf "$TMP/$f" -C "$M" && rm -f "$TMP/$f" ;;
        *.tar.gz)  tar xzf "$TMP/$f" -C "$M" && rm -f "$TMP/$f" ;;
        *)         mv "$TMP/$f" "$M/" ;;
      esac
    else
      mark_fail supertonic-ja-download
    fi
  else
    mark_fail "supertonic-ja-not-found-in-assets"
  fi
fi

step "6/6 NLLB-200 distilled 600M -> CTranslate2 int8 (2.5 GB download + on-device conversion; the long one)"
"$VENV/bin/pip" install -q torch transformers protobuf || mark_fail pip-torch
if [ -f "$M/nllb-600m-int8/model.bin" ]; then
  echo "converted model already present, skipping conversion"
else
  "$VENV/bin/ct2-transformers-converter" --model facebook/nllb-200-distilled-600M \
    --output_dir "$M/nllb-600m-int8" --quantization int8 --force \
    || mark_fail nllb-convert
fi
"$HF" download facebook/nllb-200-distilled-600M \
    sentencepiece.bpe.model tokenizer_config.json special_tokens_map.json \
    --local-dir "$M/nllb-tokenizer" >/dev/null || mark_fail nllb-tokenizer
"$HF" download facebook/nllb-200-distilled-600M tokenizer.json \
    --local-dir "$M/nllb-tokenizer" >/dev/null 2>&1 || true
# torch was only needed for the one-time conversion. pip uninstall does NOT
# remove dependencies — the aarch64 torch wheel targets NVIDIA ARM servers and
# drags in ~3.6 GB of CUDA libraries that can never run on a Pi. Remove them all.
"$VENV/bin/pip" uninstall -q -y torch triton sympy networkx mpmath || true
"$VENV/bin/pip" list 2>/dev/null | awk '/^nvidia-/{print $1}' \
  | xargs -r "$VENV/bin/pip" uninstall -q -y || true
"$VENV/bin/pip" cache purge >/dev/null 2>&1 || true
rm -rf ~/.cache/huggingface/hub/models--facebook--nllb-200-distilled-600M
rmdir "$TMP" 2>/dev/null

step "manifest"
du -sh "$M"/* 2>/dev/null
echo
if [ -n "$FAIL" ]; then
  echo "RESULT: FAILURES:$FAIL"
  exit 1
else
  echo "RESULT: ALL OK $(date)"
  exit 0
fi
