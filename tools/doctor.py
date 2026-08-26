#!/usr/bin/env python3
"""Translator V2 doctor: checks everything, speaks plainly.

Run:  venv/bin/python tools/doctor.py
When it says SPEAK, talk normally with the mic clipped where you'd wear it.
"""
import os
import subprocess
import time

import numpy as np

os.environ.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
DJI = ("alsa_input.usb-DJI_Technology_Co.__Ltd._Wireless_Mic_Rx_"
       "<RECEIVER-SERIAL>-01.analog-stereo")
MAC = "<EARBUDS-MAC>"
GOOD, BAD, WARN = "  [ OK ]", "  [PROBLEM]", "  [ ?? ]"
problems = []


def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except Exception as e:
        return 1, str(e)


def say(ok, text, problem=None):
    print((GOOD if ok else BAD) + " " + text, flush=True)
    if not ok and problem:
        problems.append(problem)


print("== Translator V2 health check ==\n", flush=True)

# 1. processes
rc, out = run(["pgrep", "-af", "python"])
pipelines = [l for l in out.splitlines() if "stage3_main" in l]
stale = [l for l in out.splitlines()
         if any(k in l for k in ("load_all_models", "stage2_main",
                                 "run_proto", "latency_test"))]
say(len(pipelines) <= 1, f"pipeline processes running: {len(pipelines)}",
    "more than one translator running at once — they fight over the computer")
say(not stale, f"leftover test programs: {len(stale)}",
    "old test programs still running and eating memory")

# 2. memory
free_kb = int([l for l in open("/proc/meminfo")][2].split()[1])
say(free_kb > 1500000, f"memory available: {free_kb/1e6:.1f} GB",
    "memory is tight — models slow to a crawl when memory runs out")

# 3. temperature
rc, out = run(["vcgencmd", "measure_temp"])
try:
    temp = float(out.split("=")[1].split("'")[0])
    say(temp < 78, f"temperature: {temp:.0f}°C",
        "the Pi is overheating and slowing itself down")
except Exception:
    print(WARN + " could not read temperature", flush=True)

# 4. AirPods (the earbud side — SEPARATE from the microphone side)
rc, out = run(["bluetoothctl", "info", MAC])
connected = "Connected: yes" in out
say(connected, "AirPods connected: " + ("yes" if connected else "NO"),
    "earbuds are not connected — open the case near the Pi")
rc, out = run(["pw-cli", "ls", "Node"])
sink = "bluez_output" in out
mic_open = "bluez_input" in out
say(sink or not connected, "AirPods audio output exists: "
    + ("yes" if sink else "NO"),
    "earbuds connected but no audio path — Bluetooth needs a restart")
say(not mic_open, "AirPods microphone stays OFF (required): "
    + ("correct, off" if not mic_open else "IT IS ON — this breaks audio"),
    "something opened the AirPods mic — audio quality collapses when this "
    "happens")
if sink:
    rc, out = run(["bash", "-c",
                   "XDG_RUNTIME_DIR=/run/user/1000 pw-dump 2>/dev/null | "
                   "grep -o '\"api.bluez5.codec\": \"[a-z_]*\"' | head -1"])
    codec = out.strip().split('"')[-2] if '"' in out else "?"
    say(codec in ("sbc", "sbc_xq", "aac"), f"earbud sound codec: {codec}",
        "earbuds are in phone-call mode (bad quality) instead of music mode")

# 5. DJI microphone side
rc, out = run(["pw-cli", "ls", "Node"])
say(DJI.split(".")[1][:12] in out or "Wireless_Mic" in out,
    "DJI receiver present: " + ("yes" if "Wireless_Mic" in out else "NO"),
    "the mic receiver is missing — check the USB cable")
rc, out = run(["amixer", "-c", "Rx", "cget", "numid=4"])
vol_ok = ": values=151" in out
say(vol_ok, "receiver volume on the Pi: " + ("maximum (correct)" if vol_ok
    else "TURNED DOWN"),
    "the receiver's volume on the Pi got turned down — fixable with one "
    "command")

# 6. the big one: live microphone level, worn position
print("\n>>> SPEAK NORMALLY NOW for 6 seconds — mic clipped where you "
      "actually wear it <<<\n", flush=True)
p = subprocess.Popen(["pw-record", "--target", DJI, "--rate", "16000",
                      "--channels", "2", "--format", "s16", "-"],
                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
buf = p.stdout.read(16000 * 2 * 2 * 6)
p.terminate()
x = np.frombuffer(buf, dtype=np.int16).reshape(-1, 2).astype(np.float32) / 32768
lvl = {}
for ch, name in ((0, "TX1 black"), (1, "TX2 grey")):
    w = x[:, ch]
    win = 1600
    r = np.sqrt(np.mean(w[:len(w)//win*win].reshape(-1, win) ** 2, axis=1))
    lvl[name] = 20 * np.log10(max(np.percentile(r, 90), 1e-6))
from src import config as _cfg
gain = _cfg.CAPTURE_GAIN_DB
for name, v in lvl.items():
    print(f"     {name}: {v:6.1f} dB raw  ->  {v+gain:6.1f} dB after the "
          f"pipeline's +{gain:.0f} dB gain", flush=True)
loudest = max(lvl.values()) + gain
if loudest > -30:
    say(True, "microphone level: GOOD — the system can hear you properly")
elif loudest > -38:
    say(False, "microphone level: QUIET — it will misunderstand you sometimes",
        "mic level is low: raise Gain in the DJI Mimo app (+6 or +12)")
elif loudest > -55:
    say(False, "microphone level: FAR TOO QUIET — this explains wrong "
        "transcriptions",
        "mic level is much too low: raise Gain in the DJI Mimo app to +12, "
        "or clip the mic closer to your mouth")
else:
    say(False, "microphone heard almost nothing — were you talking?",
        "if you were talking: mic level is critically low — DJI Mimo app "
        "Gain, or the transmitter is off/asleep")

# verdict
print("\n== VERDICT ==", flush=True)
if not problems:
    print("Everything checks out. If translations are still wrong, run this "
          "again and make sure you speak during the level test.", flush=True)
else:
    for i, pr in enumerate(problems, 1):
        print(f"{i}. {pr}", flush=True)
