#!/usr/bin/env python3
"""Generate the Test B channel-separation test file (English left, Spanish right).

Modes:
  equal        both channels at full speech level, simultaneous.
               (Original Test B — passed by ear 2026-08-26.)
  quiet-right  LEFT = full-level English speech, RIGHT = near-silence with a soft
               tick every 5 s so the wearer knows the bud is live. Listen in the
               RIGHT bud for ghosts of the English. Joint stereo's worst case,
               and the translator's normal operating condition.
  quiet-left   the mirror image (Spanish right at full level, left near-silent).

Usage: venv/bin/python tools/make_testb_wav.py <mode> <out.wav>
Play:  pw-play --target <airpods sink id> <out.wav>
"""
import os
import sys
import wave

import numpy as np

from piper import PiperVoice

M = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/models"
EN = ("Left ear. This is the English channel. You should hear only English in "
      "this ear. If you can hear Spanish, the channels are leaking. "
      "Counting: one, two, three, four, five.")
ES = ("Oido derecho. Este es el canal espanol. Solo debes escuchar espanol en "
      "este oido. Si escuchas ingles, los canales se estan mezclando. "
      "Contando: uno, dos, tres, cuatro, cinco.")


def synth(model, text):
    v = PiperVoice.load(model)
    parts = [c.audio_int16_array for c in v.synthesize(text)]
    return np.concatenate(parts).astype(np.float32) / 32768.0, v.config.sample_rate


def ticks(n, sr, level_db=-30.0):
    """Near-silence with a 0.1 s 440 Hz tick every 5 s."""
    out = np.zeros(n, dtype=np.float32)
    amp = 10 ** (level_db / 20)
    tick = amp * np.sin(2 * np.pi * 440 * np.arange(int(0.1 * sr)) / sr).astype(np.float32)
    for start in range(0, n - len(tick), 5 * sr):
        out[start:start + len(tick)] = tick
    return out


mode, out_path = sys.argv[1], sys.argv[2]
L, sr = synth(M + "/piper/en_US-lessac-medium.onnx", EN)
R, sr2 = synth(M + "/piper/es_ES-davefx-medium.onnx", ES)
assert sr == sr2
n = max(len(L), len(R))
L = np.pad(L, (0, n - len(L)))
R = np.pad(R, (0, n - len(R)))

if mode == "quiet-right":
    R = ticks(n, sr)
elif mode == "quiet-left":
    L = ticks(n, sr)
elif mode != "equal":
    sys.exit(f"unknown mode {mode!r} (use equal | quiet-right | quiet-left)")

stereo = np.tile(np.stack([L, R], axis=1), (2, 1))  # whole program twice
pcm = (np.clip(stereo, -1, 1) * 32767).astype(np.int16)
w = wave.open(out_path, "wb")
w.setnchannels(2)
w.setsampwidth(2)
w.setframerate(sr)
w.writeframes(pcm.tobytes())
w.close()
print(f"wrote {out_path}: mode={mode}, {len(pcm)/sr:.1f} s stereo @ {sr} Hz")
