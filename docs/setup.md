# Setup Guide

The full build, start to finish. The README has a condensed version — this one includes the parts that are easy to get wrong.

---

## 1. Parts

| Part | Notes |
|---|---|
| Raspberry Pi 5, 8 GB | Four Cortex-A76 cores. The 8 GB model is not strictly required (~2 GB resident) but leaves headroom. |
| Official Active Cooler | **Not optional.** Thermal pads only, no paste. Throttling at 85 °C costs 37% of your translation speed. |
| Raspberry Pi Touch Display 2, 5″ | 720×1280, portrait-native, capacitive touch. |
| SanDisk 64 GB High Endurance microSDXC | Endurance-rated, not general-purpose. A battery device gets hard-cut, and ordinary cards degrade under that. |
| Waveshare UPS HAT (E) + 4× 21700 cells | I²C fuel gauge at `0x2d`. |
| DJI Mic Mini kit | Two transmitters, one USB receiver. Class-compliant — no vendor software. |
| NFHK 90° **down-angled** USB-A → USB-C adapter | The angle matters; other adapters foul the display stack. |
| Longer official Pi 5 DSI FPC ribbon | The stock 200 mm ribbon does not reach in a stacked layout. |
| Pi 5 RTC battery | For offline timekeeping. |
| AirPods Pro (or any A2DP stereo earbuds) | Used as one stereo sink, hard-panned. |
| RasTech 27 W GaN PD USB-C supply, 5.1 V / 5 A | Feeds the UPS HAT's USB-C input. 5 A because the HAT charges the pack and carries the system load at once. |

Roughly $400 total. **[`hardware.md`](hardware.md) has a link and a reason for every part** — read it before substituting anything, because three of these choices are load-bearing (the down-angled adapter, the high-endurance card, and the 5 A supply).

---

## 2. Assembly

1. **Display ribbon → the `CAM/DISP 0` connector**, not the PCIe port. 22-pin at the Pi, 15-pin at the display.
2. **Display power → GPIO pin 2 (5 V) and pin 6 (GND).** The ribbon does not carry power. If the panel stays dark and `/sys/class/drm` shows no DSI connector, this or the ribbon seating is why — both FPC latches are easy to leave half-closed.
3. **Fan → the 4-pin `FAN` header.**
4. **DJI receiver → a black USB 2.0 port** via the 90° adapter. Never the blue USB 3.0 pair.
5. **RTC battery → the `BAT` JST connector.**
6. **Mains → the UPS HAT's USB-C input**, not the Pi's. Once the HAT is fitted it powers the Pi and charges the pack together, and the Pi's own jack goes unused.

Order matters slightly: fit the cooler before the HAT, and seat the display ribbon before stacking, because both connectors become hard to reach afterwards.

Two things to verify with your hands before software:

- **Stereo mode on the receiver** is a hardware toggle — double-press the link button. Without it both microphones are mixed into one channel and speaker arbitration is impossible.
- **Noise cancelling on the transmitters** is a single press each, and is trivially bumped against a shirt. It is tuned for ambient noise, not for a second talker, and it degrades recognition. Leave it off.

---

## 3. Operating system and dependencies

Raspberry Pi OS 64-bit (Debian 13 trixie or newer). Then:

```bash
sudo apt update
sudo apt install -y python3-venv fonts-noto-core fonts-noto-cjk fonts-noto-color-emoji i2c-tools

git clone https://github.com/TobyM-engineering/Real-Time-Speech-Translator-V2.git
cd Real-Time-Speech-Translator-V2
python3 -m venv venv
venv/bin/pip install sherpa-onnx faster-whisper ctranslate2 piper-tts sentencepiece \
                     transformers numpy soundfile huggingface_hub smbus2 PySide6
```

The Noto fonts matter: without them the language picker renders Chinese, Japanese, Korean and Indic scripts as empty boxes.

> **A trap worth knowing about.** Installing PyTorch on aarch64 pulls in ~3 GB of NVIDIA CUDA libraries that `pip uninstall torch` does not remove. Nothing here needs PyTorch at runtime. If you ever install it temporarily, remove the `nvidia-*` and `triton` packages explicitly afterwards.

---

## 4. Models

```bash
tools/fetch_models.sh
```

Idempotent — safe to re-run, and it resumes. Fetches about 5 GB: Silero VAD, SenseVoice, Parakeet TDT v3, whisper base, NLLB-200-distilled-600M, and the Piper voices.

For the fast translation path, also fetch the opus-mt pairs for the languages you actually use:

```bash
venv/bin/python tools/fetch_opus_pairs.py --solid9
```

That is ~4 GB for 45 directed pairs among the nine strongest languages. Any pair without an opus model falls back to NLLB automatically — slower, but it always works, and the fallback is logged rather than silent.

---

## 5. Device settings

```bash
cp device.example.json device.json
```

Fill in two values.

**Your receiver's PipeWire node name** — plug it in, then:

```bash
pw-cli ls Node | grep -i Rx
```

Copy the full `node.name`, which looks like `alsa_input.usb-DJI_Technology_Co.__Ltd._Wireless_Mic_Rx_<SERIAL>-01.analog-stereo`.

**Your earbuds' MAC address:**

```bash
bluetoothctl devices
```

> ⚠️ `device.json` is gitignored. It holds identifiers specific to your hardware — keep it that way.

---

## 6. Bluetooth

Enable I²C for the battery gauge while you are here:

```bash
sudo raspi-config nonint do_i2c 0
```

Then create `~/.config/wireplumber/wireplumber.conf.d/50-translator-bluez.conf`:

```
monitor.bluez.properties = {
  bluez5.enable-sbc-xq = true
  bluez5.codecs = [ sbc_xq sbc ]
  bluez5.roles = [ a2dp_source ]
  bluetooth.autoswitch-to-headset-profile = false
}
```

Two things this does, both load-bearing:

- **`bluez5.roles = [ a2dp_source ]` removes the headset profile entirely.** The failure it prevents: if anything opens the earbuds' microphone, Bluetooth switches to the headset profile and audio collapses to 8 kHz mono for both people. With the role removed there is no profile to switch to, and PipeWire exposes no microphone node at all.
- **A trap in that setting's name:** `a2dp_source` describes the *Pi's* role — streaming out to earbuds. Setting `a2dp_sink` instead makes the Pi advertise itself as a speaker, and earbud connections fail with `br-connection-profile-unavailable`.

Then pair:

```bash
bluetoothctl
power on
scan on
pair XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
connect XX:XX:XX:XX:XX:XX
```

Use the Pi's **onboard** radio, not a USB dongle. V1 died repeatedly on an RTL8761B dongle under sustained load, and essentially every recommended Linux Bluetooth dongle uses that chipset family. The onboard Infineon radio is genuinely different silicon and has run for hours here without a single write failure.

Optionally, let the recovery ladder restart the Bluetooth stack without a password — create `/etc/sudoers.d/011_translator-bt` with `visudo`:

```
your_user ALL=(root) NOPASSWD: /usr/bin/systemctl restart bluetooth
```

Exactly that one command, nothing broader. Without it the ladder still runs its first two rungs and logs loudly when the third is denied.

---

## 7. First run

```bash
tools/run_pipeline.sh
```

Watch `logs/run_<timestamp>.log`. You want `READY pipeline ready` — about 30 s cold, 13 s warm. The launcher refuses to start a second instance.

Then check the hardware:

```bash
venv/bin/python tools/doctor.py
```

It speaks plain language: microphone levels, whether the two channels really carry separate signals, earbud state, temperature, memory. **Run this before believing anything is broken in software.**

Two failure modes it exists to catch:

- **Mono mode.** If the receiver's stereo toggle is off, both microphones land on one channel and nobody can be told apart. The pipeline also self-checks this at startup.
- **Low microphone level.** Worn speech should sit around −21 to −26 dBFS. If it collapses, **power-cycle both transmitters first** — a transient transmitter state once cost a full day of debugging that looked exactly like a software regression. Digital gain is a last resort, not a first move.

---

## 8. Verify it end to end

Before trusting it in a conversation, confirm each stage in order. Every one of these is a real failure mode that has happened at least once here.

| Check | How | Expected |
|---|---|---|
| Both channels carry separate signal | `venv/bin/python tools/doctor.py` | "channels distinct", not "SUSPECT MONO MODE" |
| Worn microphone level | doctor, speaking in wearing position | speech around −21 to −26 dBFS, ≥10 dB over the room |
| Speaker separation | speak into TX1 only, watch the log | `SEG ch=A … ratio +10` or better, and `ch=B … DROP_BLEED` |
| Recognition | speak a full sentence | `ASR turn#N` with your actual words |
| Translation | same turn | `MT turn#N ->` with the other language |
| Audio routing | same turn | `PLAY ear=B` for a turn spoken by A — **never `ear=A`** |
| Anti-feedback | speak while a translation is playing into your own ear | `GATE_TRIM` or `GATE_DISCARD`, not a re-translation loop |
| Battery gauge | `SUP BATT` lines in the log | a percentage and per-cell millivolts |

If the ear routing is ever backwards, stop and check the windscreen convention: **TX1 black = left = Person A**, TX2 grey = right = Person B.

---

## 9. Autostart

```bash
cp tools/translator.service ~/.config/systemd/user/
# edit WorkingDirectory and ExecStart for your username
systemctl --user daemon-reload
systemctl --user enable translator.service
loginctl enable-linger
```

`loginctl enable-linger` is what lets the service run without an interactive login. `Restart=on-failure` doubles as boot ordering: Qt exits non-zero until the compositor's Wayland socket exists, so the service simply retries until the desktop is up. Expect one by-design retry in the log on every cold boot.

Power-on to ready on the glass: **about 46 seconds.**

For development, stop it and run by hand:

```bash
systemctl --user stop translator.service
tools/run_pipeline.sh
```

---

## 10. If it does not come up

| Symptom | Check |
|---|---|
| Nothing on screen, Pi is running | `ls /sys/class/drm \| grep DSI`. Nothing? The panel did not probe — reseat the ribbon at both ends and the 5 V/GND jumpers, power off. The log says so too. |
| Service keeps restarting | `systemctl --user status translator.service` and the run log. Qt errors mean the compositor is not up; check `pgrep labwc`. |
| Ready, but no audio in the earbuds | The on-screen fault pill will say if they are disconnected. Otherwise `bluetoothctl info <MAC>`. |
| No transcripts | `venv/bin/python tools/doctor.py`. Usually a transmitter that is off, in mono mode, or out of range. |
| Nonsense transcripts | Check the language picker — speech in a language other than the one selected decodes as confident nonsense in the selected language. |
