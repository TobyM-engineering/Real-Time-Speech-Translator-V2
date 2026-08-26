#!/usr/bin/env python3
"""Translator V2 — measure real end-to-end turn latency:
from the acoustic end of speech to translated audio ready/playing.

Modes:
  --mic          live DJI capture (left channel = TX1), English -> Spanish (whisper)
  --wav FILE     feed FILE through the same pipeline in real time (paced playback)
  --src X        en (whisper) | ja | zh (SenseVoice). Non-en translates to English.

All models are preloaded and warmed before capture starts (the script prints READY).
Timing is derived from the audio stream position, so it includes VAD endpoint wait
and capture buffering — the number is what a user would actually experience,
except playback start, which is a separately-measured pw-play spawn overhead.
"""
import argparse
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path("<REPO-ROOT>")
M = ROOT / "models"
DJI_NODE = ("alsa_input.usb-DJI_Technology_Co.__Ltd._Wireless_Mic_Rx_"
            "<RECEIVER-SERIAL>-01.analog-stereo")
SR = 16000
CHUNK = 512  # samples per VAD feed
os.environ.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")

ap = argparse.ArgumentParser()
ap.add_argument("--mic", action="store_true")
ap.add_argument("--wav")
ap.add_argument("--src", default="en", choices=["en", "ja", "zh"])
ap.add_argument("--duration", type=float, default=75.0)
ap.add_argument("--out", default=str(ROOT / "models"))
args = ap.parse_args()
OUT = Path(args.out)
OUT.mkdir(parents=True, exist_ok=True)

T0 = time.time()
def log(*x):
    print(f"[{time.time()-T0:7.2f}]", *x)
    sys.stdout.flush()

# ---------------------------------------------------------------- models
log(f"loading models for src={args.src} ...")
import sherpa_onnx as so

if args.src == "en":
    from faster_whisper import WhisperModel
    wm = WhisperModel(str(M / "whisper-base-ct2"), device="cpu",
                      compute_type="int8", cpu_threads=3)
    def asr_fn(x):
        segs, _ = wm.transcribe(x, language="en", beam_size=1,
                                condition_on_previous_text=False,
                                without_timestamps=True)
        return " ".join(s.text.strip() for s in segs).strip()
    src_lang, tgt_piper = "eng_Latn", M / "piper/es_ES-davefx-medium.onnx"
    tgt_lang = "spa_Latn"
else:
    rec = so.OfflineRecognizer.from_sense_voice(
        model=str(M / "sensevoice/model.int8.onnx"),
        tokens=str(M / "sensevoice/tokens.txt"),
        num_threads=3, use_itn=True, language="auto")
    def asr_fn(x):
        st = rec.create_stream()
        st.accept_waveform(SR, x)
        rec.decode_stream(st)
        return st.result.text.strip()
    src_lang = "jpn_Jpan" if args.src == "ja" else "zho_Hans"
    tgt_piper, tgt_lang = M / "piper/en_US-lessac-medium.onnx", "eng_Latn"

import ctranslate2
import transformers
tok = transformers.AutoTokenizer.from_pretrained(str(M / "nllb-tokenizer"),
                                                 src_lang=src_lang)
mt = ctranslate2.Translator(str(M / "nllb-600m-int8"), device="cpu",
                            compute_type="int8", inter_threads=1, intra_threads=3)
def mt_fn(text):
    toks = tok.convert_ids_to_tokens(tok.encode(text))
    out = mt.translate_batch([toks], target_prefix=[[tgt_lang]], beam_size=1)
    hyp = out[0].hypotheses[0]
    return tok.decode(tok.convert_tokens_to_ids(hyp[1:]),
                      skip_special_tokens=True).strip()

from piper import PiperVoice
voice = PiperVoice.load(str(tgt_piper))
TTS_SR = voice.config.sample_rate

vc = so.VadModelConfig()
vc.silero_vad.model = str(M / "silero_vad.onnx")
vc.silero_vad.min_silence_duration = 0.5
vc.silero_vad.min_speech_duration = 0.25
vc.sample_rate = SR
vad = so.VoiceActivityDetector(vc, buffer_size_in_seconds=150)

# ---------------------------------------------------------------- warmup
log("warming up (first-call kernel/cache costs paid now, not in the measurement)")
asr_fn(np.zeros(SR, dtype=np.float32))
mt_fn("Hello there.")
for _ in voice.synthesize("Hola."):
    pass

# ---------------------------------------------------------------- turn
totals = []
def process_turn(k, seg_samples, t_acoustic_end):
    t_fire = time.time()
    dur = len(seg_samples) / SR
    t0 = time.time()
    text = asr_fn(np.asarray(seg_samples, dtype=np.float32))
    t_asr = time.time() - t0
    if not text:
        log(f"TURN {k}: VAD segment of {dur:.1f} s produced no text, skipped")
        return
    t0 = time.time()
    translated = mt_fn(text)
    t_mt = time.time() - t0
    t0 = time.time()
    first = None
    parts = []
    for ch in voice.synthesize(translated):
        if first is None:
            first = time.time() - t0
        parts.append(ch.audio_int16_array)
    t_tts = time.time() - t0
    audio = np.concatenate(parts) if parts else np.zeros(1, dtype=np.int16)
    wav_path = OUT / f"turn{k}.wav"
    w = wave.open(str(wav_path), "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(TTS_SR)
    w.writeframes(audio.tobytes()); w.close()
    subprocess.Popen(["pw-play", str(wav_path)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t_ready = time.time()
    endpoint = t_fire - t_acoustic_end
    total = t_ready - t_acoustic_end
    potential = total - t_tts + (first or 0)
    totals.append((total, potential))
    log(f'TURN {k}  speech {dur:.1f} s  heard: "{text}"')
    log(f'        -> "{translated}"')
    log(f"        endpoint-detect {endpoint:.2f}  ASR {t_asr:.2f}  "
        f"translate {t_mt:.2f}  TTS {t_tts:.2f} (first chunk {first:.2f})")
    log(f"        TOTAL stop-speaking -> audio handed to output: {total:.2f} s"
        f"   (streaming-TTS potential: {potential:.2f} s)")

# ---------------------------------------------------------------- capture
def feed_samples(get_chunks):
    """get_chunks yields (float32 mono 16k chunk, t_wall_of_last_sample)."""
    k = 0
    consumed = 0
    for chunk, t_last in get_chunks:
        i = 0
        while i < len(chunk):
            vad.accept_waveform(chunk[i:i + CHUNK])
            i += CHUNK
        consumed += len(chunk)
        while not vad.empty():
            seg = vad.front
            k += 1
            seg_end_sample = seg.start + len(seg.samples)
            # wall time of that stream position:
            t_end = t_last - (consumed - seg_end_sample) / SR
            samples = np.array(seg.samples, dtype=np.float32)
            vad.pop()
            process_turn(k, samples, t_end)

def mic_chunks():
    cap = OUT / "latency_capture.wav"
    cap.unlink(missing_ok=True)
    rec_p = subprocess.Popen(
        ["pw-record", "--rate", str(SR), "--channels", "2", "--format", "s16",
         "--target", DJI_NODE, str(cap)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # wait for the data chunk to appear, then tail
        f = None
        t_wait = time.time()
        while time.time() - t_wait < 10:
            if cap.exists() and cap.stat().st_size > 256:
                f = open(cap, "rb")
                hdr = f.read(512)
                i = hdr.find(b"data")
                if i >= 0:
                    f.seek(i + 8)
                    break
                f.close(); f = None
            time.sleep(0.05)
        if f is None:
            log("FATAL: capture file never started"); rec_p.terminate(); return
        log(f"READY - capturing for {args.duration:.0f} s, speak now")
        rest = b""
        t_stop = time.time() + args.duration
        while time.time() < t_stop:
            b = f.read()
            if not b:
                time.sleep(0.03)
                continue
            t_last = time.time()
            b = rest + b
            n = len(b) // 4 * 4  # 2 ch x int16
            rest = b[n:]
            frames = np.frombuffer(b[:n], dtype=np.int16).reshape(-1, 2)
            left = frames[:, 0].astype(np.float32) / 32768.0
            yield left, t_last
        f.close()
    finally:
        rec_p.terminate()

def wav_chunks(path):
    w = wave.open(path, "rb")
    nch, fr, nf = w.getnchannels(), w.getframerate(), w.getnframes()
    raw = w.readframes(nf)
    w.close()
    x = np.frombuffer(raw, dtype=np.int16).reshape(-1, nch)[:, 0]
    x = x.astype(np.float32) / 32768.0
    if fr != SR:  # linear resample is fine for a timing test
        x = np.interp(np.arange(0, len(x), fr / SR), np.arange(len(x)), x)
    x = np.concatenate([x, np.zeros(SR, dtype=np.float32)])  # silence tail
    log(f"READY - feeding {len(x)/SR:.1f} s of {path} in real time")
    step = 1600  # 100 ms
    i = 0
    while i < len(x):
        time.sleep(step / SR)
        i += step
        yield x[max(0, i - step):i], time.time()

if args.mic:
    feed_samples(mic_chunks())
elif args.wav:
    feed_samples(wav_chunks(args.wav))
else:
    sys.exit("need --mic or --wav")

vad.flush()
log("========== SUMMARY ==========")
if totals:
    ts = sorted(t for t, _ in totals)
    ps = sorted(p for _, p in totals)
    log(f"turns: {len(ts)}   stop-speaking -> audio: "
        f"min {ts[0]:.2f}  median {ts[len(ts)//2]:.2f}  max {ts[-1]:.2f} s")
    log(f"with streaming TTS (potential):            "
        f"min {ps[0]:.2f}  median {ps[len(ps)//2]:.2f}  max {ps[-1]:.2f} s")
else:
    log("no turns captured")
