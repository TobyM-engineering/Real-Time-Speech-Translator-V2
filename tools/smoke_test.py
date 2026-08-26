#!/usr/bin/env python3
"""Translator V2 — smoke-test every model in the offline stack, sequentially.

Usage: venv/bin/python tools/smoke_test.py <input_stereo_48k.wav> <out_dir>
The input should be a Test-A-style recording (left channel = TX1, English speech).
Each stage is independent: a failure is reported and the rest still run.
Models are loaded with 3 threads (leave one core for PipeWire/UI, per CLAUDE.md).
"""
import gc
import subprocess
import sys
import time
import traceback
import wave
from pathlib import Path

import numpy as np

ROOT = Path("<REPO-ROOT>")
M = ROOT / "models"
IN_WAV = Path(sys.argv[1])
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

results = []


def report(name, ok, msg):
    results.append((name, ok, msg))
    print(f"    {'OK' if ok else 'FAIL'}: {msg}")


def read_left_16k(path):
    """Left channel of a 48 kHz stereo wav -> float32 mono 16 kHz.
    Decimation by mean-of-3 is a crude anti-alias filter; fine for a smoke test,
    the real pipeline will resample properly."""
    w = wave.open(str(path), "rb")
    nch, fr, nf = w.getnchannels(), w.getframerate(), w.getnframes()
    raw = w.readframes(nf)
    w.close()
    assert fr == 48000, f"expected 48 kHz, got {fr}"
    a = np.frombuffer(raw, dtype=np.int16).reshape(-1, nch)
    left = a[:, 0].astype(np.float32) / 32768.0
    n = len(left) // 3
    return left[: n * 3].reshape(-1, 3).mean(axis=1), 16000


def write_wav(path, samples, rate):
    s = np.clip(np.asarray(samples, dtype=np.float32), -1, 1)
    w = wave.open(str(path), "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes((s * 32767).astype(np.int16).tobytes())
    w.close()


samples, rate = read_left_16k(IN_WAV)
dur_in = len(samples) / rate
print(f"input: {IN_WAV.name}, left channel, {dur_in:.1f} s @ {rate} Hz")

import sherpa_onnx as so

# --- 1. Silero VAD ---------------------------------------------------------
print("\n=== 1/6 Silero VAD")
try:
    vc = so.VadModelConfig()
    vc.silero_vad.model = str(M / "silero_vad.onnx")
    vc.sample_rate = 16000
    vad = so.VoiceActivityDetector(vc, buffer_size_in_seconds=30)
    t0 = time.time()
    i = 0
    segs = []
    while i < len(samples):
        vad.accept_waveform(samples[i : i + 512])
        i += 512
    vad.flush()
    while not vad.empty():
        segs.append(len(vad.front.samples) / 16000)
        vad.pop()
    report("silero-vad", len(segs) > 0,
           f"{len(segs)} speech segment(s), total {sum(segs):.1f} s of {dur_in:.1f} s, "
           f"processed in {time.time()-t0:.2f} s")
except Exception as e:
    report("silero-vad", False, e); traceback.print_exc()

# --- 2. SenseVoice ---------------------------------------------------------
print("\n=== 2/6 SenseVoice small int8 (ASR)")
sense_text = ""
try:
    t0 = time.time()
    rec = so.OfflineRecognizer.from_sense_voice(
        model=str(M / "sensevoice/model.int8.onnx"),
        tokens=str(M / "sensevoice/tokens.txt"),
        num_threads=3, use_itn=True, language="auto")
    t_load = time.time() - t0
    st = rec.create_stream()
    st.accept_waveform(16000, samples)
    t0 = time.time()
    rec.decode_stream(st)
    t_dec = time.time() - t0
    sense_text = st.result.text.strip()
    report("sensevoice", bool(sense_text),
           f'load {t_load:.1f} s, decode {t_dec:.2f} s for {dur_in:.1f} s audio -> "{sense_text}"')
    del rec, st; gc.collect()
except Exception as e:
    report("sensevoice", False, e); traceback.print_exc()

# --- 3. faster-whisper base int8 ------------------------------------------
print("\n=== 3/6 faster-whisper base int8 (ASR)")
whisper_text = ""
try:
    from faster_whisper import WhisperModel
    t0 = time.time()
    wm = WhisperModel(str(M / "whisper-base-ct2"), device="cpu",
                      compute_type="int8", cpu_threads=3)
    t_load = time.time() - t0
    t0 = time.time()
    seg_iter, info = wm.transcribe(samples, language="en", beam_size=1)
    whisper_text = " ".join(s.text.strip() for s in seg_iter).strip()
    t_dec = time.time() - t0
    report("whisper", bool(whisper_text),
           f'load {t_load:.1f} s, decode {t_dec:.2f} s for {dur_in:.1f} s audio -> "{whisper_text}"')
    del wm; gc.collect()
except Exception as e:
    report("whisper", False, e); traceback.print_exc()

# --- 4. NLLB translate en -> es -------------------------------------------
print("\n=== 4/6 NLLB-200 600M int8 (translation)")
es_text = ""
try:
    import ctranslate2
    import transformers
    src_text = whisper_text or "This is a test of the translator."
    t0 = time.time()
    tok = transformers.AutoTokenizer.from_pretrained(str(M / "nllb-tokenizer"),
                                                     src_lang="eng_Latn")
    tr = ctranslate2.Translator(str(M / "nllb-600m-int8"), device="cpu",
                                compute_type="int8", inter_threads=1, intra_threads=3)
    t_load = time.time() - t0
    t0 = time.time()
    src_tokens = tok.convert_ids_to_tokens(tok.encode(src_text))
    out = tr.translate_batch([src_tokens], target_prefix=[["spa_Latn"]])
    hyp = out[0].hypotheses[0]
    es_text = tok.decode(tok.convert_tokens_to_ids(hyp[1:]),
                         skip_special_tokens=True).strip()
    t_tr = time.time() - t0
    report("nllb", bool(es_text),
           f'load {t_load:.1f} s, translate {t_tr:.2f} s -> "{es_text}"')
    del tr, tok; gc.collect()
except Exception as e:
    report("nllb", False, e); traceback.print_exc()

# --- 5. Piper Spanish TTS --------------------------------------------------
print("\n=== 5/6 Piper es_ES medium (TTS)")
try:
    text = es_text or "Hola, esta es una prueba del traductor."
    out_es = OUT / "smoke_es.wav"
    t0 = time.time()
    subprocess.run([str(ROOT / "venv/bin/piper"),
                    "-m", str(M / "piper/es_ES-davefx-medium.onnx"),
                    "-f", str(out_es)],
                   input=text.encode(), check=True, capture_output=True)
    t_syn = time.time() - t0
    w = wave.open(str(out_es), "rb")
    d = w.getnframes() / w.getframerate()
    w.close()
    report("piper-es", d > 0.5,
           f"synthesized {d:.1f} s of Spanish in {t_syn:.2f} s (RTF {t_syn/d:.2f}) -> {out_es.name}")
except Exception as e:
    report("piper-es", False, e); traceback.print_exc()

# --- 6. Supertonic-3 Japanese TTS -----------------------------------------
print("\n=== 6/6 Supertonic-3 int8 (Japanese TTS)")
try:
    S = M / "supertonic-3-int8"
    cfg = so.OfflineTtsConfig(
        model=so.OfflineTtsModelConfig(
            supertonic=so.OfflineTtsSupertonicModelConfig(
                text_encoder=str(S / "text_encoder.int8.onnx"),
                duration_predictor=str(S / "duration_predictor.int8.onnx"),
                vector_estimator=str(S / "vector_estimator.int8.onnx"),
                vocoder=str(S / "vocoder.int8.onnx"),
                voice_style=str(S / "voice.bin"),
                unicode_indexer=str(S / "unicode_indexer.bin"),
                tts_json=str(S / "tts.json")),
            num_threads=3))
    t0 = time.time()
    tts = so.OfflineTts(cfg)
    t_load = time.time() - t0
    ja = "こんにちは。これは日本語の音声合成のテストです。"
    t0 = time.time()
    audio = tts.generate(ja, sid=0, speed=1.0)
    t_syn = time.time() - t0
    d = len(audio.samples) / audio.sample_rate
    out_ja = OUT / "smoke_ja.wav"
    write_wav(out_ja, audio.samples, audio.sample_rate)
    report("supertonic-ja", d > 0.5,
           f"load {t_load:.1f} s, synthesized {d:.1f} s of Japanese in {t_syn:.2f} s "
           f"(RTF {t_syn/d:.2f}) @ {audio.sample_rate} Hz -> {out_ja.name}")
    del tts; gc.collect()
except Exception as e:
    report("supertonic-ja", False, e); traceback.print_exc()

# --- summary ---------------------------------------------------------------
print("\n========== SUMMARY ==========")
for name, ok, msg in results:
    print(f"{'PASS' if ok else 'FAIL':4}  {name}")
n_fail = sum(1 for _, ok, _ in results if not ok)
print(f"{len(results)-n_fail}/{len(results)} stages passed")
sys.exit(1 if n_fail else 0)
