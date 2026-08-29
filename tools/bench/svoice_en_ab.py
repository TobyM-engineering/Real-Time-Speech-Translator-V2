"""A/B: whisper base vs SenseVoice on English, all current dump clips.
Times, transcripts, punctuation presence, and the full en-path compute
ratio (ASR+MT+TTS per second of speech) on the longest clips."""
import os
import re
import sys
import time
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from src import config

D = "/tmp/translator_dumps"
CLIPS = [f"asr_turn{i}.wav" for i in range(1, 9)]
_SPLIT = re.compile(r"(?<=[.!?。！？…])\s+")


def load(path):
    w = wave.open(path, "rb")
    x = np.frombuffer(w.readframes(w.getnframes()),
                      dtype=np.int16).astype(np.float32) / 32768.0
    w.close()
    return x


import sherpa_onnx as so
from faster_whisper import WhisperModel
import ctranslate2
import transformers
from piper import PiperVoice

sense = so.OfflineRecognizer.from_sense_voice(
    model=f"{config.MODELS}/sensevoice/model.int8.onnx",
    tokens=f"{config.MODELS}/sensevoice/tokens.txt",
    num_threads=config.ASR_THREADS, use_itn=True, language="auto")
whisper = WhisperModel(f"{config.MODELS}/whisper-base-ct2", device="cpu",
                       compute_type="int8", cpu_threads=config.ASR_THREADS)
tok = transformers.AutoTokenizer.from_pretrained(
    f"{config.MODELS}/nllb-tokenizer", src_lang="eng_Latn")
tr = ctranslate2.Translator(f"{config.MODELS}/nllb-600m-int8", device="cpu",
                            compute_type="int8", inter_threads=1,
                            intra_threads=config.MT_THREADS)
voice = PiperVoice.load(f"{config.MODELS}/piper/es_ES-davefx-medium.onnx")

# warm everything
st = sense.create_stream()
st.accept_waveform(config.SR, np.zeros(config.SR, dtype=np.float32))
sense.decode_stream(st)
list(whisper.transcribe(np.zeros(config.SR, dtype=np.float32),
                        language="en", beam_size=1)[0])
toks = tok.convert_ids_to_tokens(tok.encode("Hello."))
tr.translate_batch([toks], target_prefix=[["spa_Latn"]], beam_size=1)
list(voice.synthesize("Hola."))


def asr_whisper(a):
    segs, _ = whisper.transcribe(a, language="en", beam_size=1,
                                 condition_on_previous_text=False,
                                 without_timestamps=True)
    return " ".join(s.text.strip() for s in segs).strip()


def asr_sense(a):
    st = sense.create_stream()
    st.accept_waveform(config.SR, a)
    sense.decode_stream(st)
    return st.result.text.strip()


def mt_es(text):
    """Pipeline-style: per sentence, greedy. Returns (out, secs, nsent, flag)"""
    t0 = time.time()
    outs, flag = [], False
    sents = [s for s in _SPLIT.split(text.strip()) if s]
    for s in sents:
        tk = tok.convert_ids_to_tokens(tok.encode(s))
        r = tr.translate_batch([tk], target_prefix=[["spa_Latn"]],
                               beam_size=1, return_scores=True)[0]
        hyp = r.hypotheses[0][1:]
        outs.append(tok.decode(tok.convert_tokens_to_ids(hyp),
                               skip_special_tokens=True).strip())
        ratio = len(hyp) / max(1, len(tk))
        if ratio > 1.25 or ratio < 0.4:   # inflation OR suspected truncation
            flag = True
    return " ".join(outs), time.time() - t0, len(sents), flag


results = {}
print("=== per-clip ASR A/B (min of 2 runs each) ===", flush=True)
for name in CLIPS:
    a = load(f"{D}/{name}")
    dur = len(a) / config.SR
    tw, ts = [], []
    for _ in range(2):
        t0 = time.time(); wt = asr_whisper(a); tw.append(time.time() - t0)
    for _ in range(2):
        t0 = time.time(); stx = asr_sense(a); ts.append(time.time() - t0)
    results[name] = (dur, min(tw), wt, min(ts), stx)
    punct = "yes" if re.search(r"[.!?]", stx[:-1] or "") or \
        (len(_SPLIT.split(stx)) > 1) else ("end-only" if stx.endswith((".", "!", "?")) else "NONE")
    print(f"\n{name} ({dur:.1f}s):", flush=True)
    print(f"  whisper    {min(tw):5.2f}s (RTF {min(tw)/dur:.3f}): \"{wt}\"",
          flush=True)
    print(f"  sensevoice {min(ts):5.2f}s (RTF {min(ts)/dur:.3f}): \"{stx}\""
          f"   [internal punct: {punct}]", flush=True)

print("\n=== full en-path compute per second of speech (ASR+MT+TTS) ===",
      flush=True)
for name in CLIPS:
    dur, wt_s, wt, ss_s, stx = results[name]
    if dur < 4.0:
        continue
    for label, asr_s, text in (("whisper   ", wt_s, wt),
                               ("sensevoice", ss_s, stx)):
        out, mt_s, nsent, flag = mt_es(text)
        t0 = time.time()
        parts = [c.audio_int16_array for c in voice.synthesize(out)]
        tts_s = time.time() - t0
        total = asr_s + mt_s + tts_s
        print(f"{name} {label}: ASR {asr_s:5.2f} + MT {mt_s:5.2f} "
              f"({nsent} sent{'  FLAG' if flag else ''}) + TTS {tts_s:4.2f} "
              f"= {total:5.2f}s for {dur:.1f}s speech -> ratio "
              f"{total/dur:.2f}x", flush=True)
