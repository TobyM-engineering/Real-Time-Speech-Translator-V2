#!/usr/bin/env python3
"""Load every model the translator uses and hold them resident, printing the
RSS cost of each load. Simulates the real pipeline's memory footprint for
soak tests and RAM budgeting. Writes READY to <flag file> when done, then
sleeps forever (kill to release).

Usage: venv/bin/python tools/load_all_models.py <flag_file>
"""
import os
import sys
import time

M = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/models"


def rss_mb():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS"):
                return int(line.split()[1]) / 1024
    return 0.0


last = rss_mb()
def step(name):
    global last
    now = rss_mb()
    print(f"{name:<28} +{now-last:7.0f} MB   (total {now:6.0f} MB)", flush=True)
    last = now


step("python + imports baseline")

import numpy as np  # noqa: E402
import sherpa_onnx as so  # noqa: E402

vc = so.VadModelConfig()
vc.silero_vad.model = f"{M}/silero_vad.onnx"
vc.sample_rate = 16000
vad = so.VoiceActivityDetector(vc, buffer_size_in_seconds=150)
step("silero VAD")

sense = so.OfflineRecognizer.from_sense_voice(
    model=f"{M}/sensevoice/model.int8.onnx",
    tokens=f"{M}/sensevoice/tokens.txt",
    num_threads=3, use_itn=True, language="auto")
step("SenseVoice int8")

from faster_whisper import WhisperModel  # noqa: E402
whisper = WhisperModel(f"{M}/whisper-base-ct2", device="cpu",
                       compute_type="int8", cpu_threads=3)
step("faster-whisper base int8")

import ctranslate2  # noqa: E402
import transformers  # noqa: E402
tok = transformers.AutoTokenizer.from_pretrained(f"{M}/nllb-tokenizer",
                                                 src_lang="eng_Latn")
nllb = ctranslate2.Translator(f"{M}/nllb-600m-int8", device="cpu",
                              compute_type="int8", inter_threads=1,
                              intra_threads=3)
step("NLLB 600M int8 + tokenizer")

from piper import PiperVoice  # noqa: E402
piper_en = PiperVoice.load(f"{M}/piper/en_US-lessac-medium.onnx")
step("Piper en_US")
piper_es = PiperVoice.load(f"{M}/piper/es_ES-davefx-medium.onnx")
step("Piper es_ES")

S = f"{M}/supertonic-3-int8"
tts_cfg = so.OfflineTtsConfig(
    model=so.OfflineTtsModelConfig(
        supertonic=so.OfflineTtsSupertonicModelConfig(
            text_encoder=f"{S}/text_encoder.int8.onnx",
            duration_predictor=f"{S}/duration_predictor.int8.onnx",
            vector_estimator=f"{S}/vector_estimator.int8.onnx",
            vocoder=f"{S}/vocoder.int8.onnx",
            voice_style=f"{S}/voice.bin",
            unicode_indexer=f"{S}/unicode_indexer.bin",
            tts_json=f"{S}/tts.json"),
        num_threads=3))
supertonic = so.OfflineTts(tts_cfg)
step("Supertonic-3 int8")

# One warm inference per engine so lazily-allocated buffers are counted too.
st = sense.create_stream(); st.accept_waveform(16000, np.zeros(16000, dtype=np.float32))
sense.decode_stream(st)
list(whisper.transcribe(np.zeros(16000, dtype=np.float32), language="en", beam_size=1)[0])
toks = tok.convert_ids_to_tokens(tok.encode("Hello there."))
nllb.translate_batch([toks], target_prefix=[["spa_Latn"]], beam_size=1)
for _ in piper_es.synthesize("Hola."):
    pass
supertonic.generate("こんにちは。", sid=0, speed=1.0)
step("after warm inference (buffers)")

print(f"TOTAL RESIDENT: {rss_mb():.0f} MB", flush=True)
if len(sys.argv) > 1:
    with open(sys.argv[1], "w") as f:
        f.write("READY\n")
print("holding models resident — kill me to release", flush=True)
while True:
    time.sleep(60)
