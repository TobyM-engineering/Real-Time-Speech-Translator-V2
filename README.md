<div align="center">

# Real-Time Speech Translator V2

**A two-person, bidirectional speech translator that runs entirely offline on a Raspberry Pi 5.**

Two people clip on a microphone and wear one earbud each. Each hears the other in their own language.
No phone, no app, no internet, no API key.

</div>

---

# 🎥 Demo

<!-- TODO (Toby): drop a short clip in media/ and link it here, as v1 does.
     Suggested shot: both people wearing a mic and one earbud, one speaks
     Spanish, the other's earbud answers in English a couple of seconds later,
     with the screen visible showing both halves. -->

*Demo video to be added.*

---

# 📸 System Photos

<!-- TODO (Toby): add photos to media/ and embed them here, e.g.
     <img src="media/board_stack.jpg" width="280">  -->

*Build photos to be added — the stacked layout (UPS HAT, Pi 5, display) and the two transmitters in wearing position.*

---

# 🌍 Project Background

[Version 1](https://github.com/TobyM-engineering/Real-Time-Speech-Translator) was one-directional and cloud-based: a Pi Zero 2 W listened to a room, streamed audio to OpenAI's realtime API, and played English into one ear. It worked, cost about $65 in parts, and about $10 per 30 minutes of API usage.

The obvious question was whether the cloud was doing anything that couldn't be done locally. **V2 is the answer: every model runs on the device.** Speech recognition, translation, and speech synthesis are all local, and the conversation never leaves the hardware.

That buys three things V1 could not have:

- **Privacy by construction.** Nothing is transmitted, so nothing can be intercepted, logged, or billed.
- **No running cost and no network.** It works on a plane, in a basement, or in a country whose data roaming you would rather not think about.
- **Two directions at once.** Two microphones, two earbuds, two languages, one device sitting between two people.

What it costs, honestly: roughly $400 in parts instead of $65, and about **2.5–4 seconds** from the end of a sentence to the translation in the other person's ear, where the cloud version managed a bit over one. The rest of this README is about that tradeoff.

---

# 🛠 System Overview

## Hardware

| Part | Notes |
|---|---|
| [Raspberry Pi 5, 8 GB](https://www.raspberrypi.com/products/raspberry-pi-5/) | aarch64, four Cortex-A76 cores. **Compute, not RAM, is the binding constraint** — the whole model stack is ~2 GB resident. |
| [Official Active Cooler](https://www.raspberrypi.com/products/active-cooler/) | Thermal **pads only**, no paste. Mandatory: at 85 °C the Pi throttles 2.4 GHz → 1.5 GHz, a 37% cut that lands directly on translation latency. |
| [Raspberry Pi Touch Display 2, 5″](https://www.raspberrypi.com/products/touch-display-2/) | 720×1280 IPS, portrait-native, 5-point capacitive touch. Runs rotated, split into two 180°-opposed halves. |
| [SanDisk 64 GB High Endurance microSDXC (SDSQQNR-064G-GN6IA)](https://www.amazon.com/dp/B07P3D6Y5B) | Boot device and model storage. **High-endurance specifically**, not a general-purpose card: this is a battery device that can lose power hard, and an ordinary card's flash translation layer degrades under repeated unclean shutdowns — the endurance line is built for continuous-write video recorders and survives it. |
| [Waveshare UPS HAT (E)](https://www.amazon.com/dp/B0DBLMFX57) | 4× 21700 cells. I²C fuel gauge at `0x2d` is read by the supervisor for the on-screen battery indicator and the low-battery fault. |
| [4× 21700 cells](https://www.amazon.com/dp/B0HDZ2MTGX) | For the UPS HAT. *Cell brand and model unverified — these are what is in this build, not a recommendation.* |
| [DJI Mic Mini kit](https://www.dji.com/mic-mini) | 2 clip-on transmitters + 1 USB receiver. Class-compliant USB audio — no DJI software involved. **TX1 (black windscreen) = left/FL = Person A. TX2 (grey) = right/FR = Person B.** |
| [NFHK 90° down-angled USB-A → USB-C adapter](https://www.amazon.com/dp/B0C7GLCWKC) | Carries the DJI receiver. **The down-angle orientation is load-bearing** — other angled adapters foul the display stack. A deliberate part choice, not a generic substitute. |
| [Longer official Pi 5 DSI FPC ribbon](https://www.amazon.com/dp/B0D12N6TLW) | Replaces the stock 200 mm ribbon so the screen reaches in the stacked layout. *Length unmeasured.* |
| [XYGStudy Pi 5 RTC battery (RTC-Bat-B)](https://www.amazon.com/dp/B0CR76SM52) | 64 mAh rechargeable, 2-pin JST. **On hand but NOT yet fitted** — an offline device has no NTP, so fit this before relying on offline mode. |
| AirPods Pro | Used as **one** stereo sink, hard-panned: left bud to one person, right bud to the other. Any A2DP stereo earbuds should work. |
| [RasTech 27 W GaN PD USB-C supply, 5.1 V / 5 A](https://www.amazon.com/dp/B0CLV6WB4L) | The only mains supply in the build, and it feeds the **UPS HAT's USB-C input**, not the Pi's — the HAT charges the 4×21700 pack and powers the Pi at the same time, which is the normal operating path (the Pi's own USB-C jack goes unused once the HAT is fitted). **The 5.1 V / 5 A rating is the reason for this part:** in that path the HAT has to carry the full system load *and* charge the pack simultaneously, so a typical 15–18 W phone charger would leave it choosing between the two. It also powers the Pi directly through its USB-C jack for bench work without the battery, which is how the build ran before the HAT and cells were fitted. |

Roughly **$400** all-in, dominated by the Pi, the display, and the DJI kit.

## Wiring

- **Display ribbon → `CAM/DISP 0`** (*not* the PCIe port), 22-pin to 15-pin.
- **Display power → GPIO pin 2 (5 V) and pin 6 (GND).** The ribbon alone does not power the panel.
- **Fan → the 4-pin `FAN` header.**
- **DJI receiver → a black USB 2.0 port**, never the blue USB 3.0 pair, via the 90° down-angled adapter.
- **RTC battery → the `BAT` JST connector.** (Not fitted in this build.)

---

# 🧩 How It Works

## Audio pipeline

```
DJI receiver (one USB device, stereo)
  ├── FL channel = Person A's microphone
  └── FR channel = Person B's microphone
        │
        ├── per-channel voice activity detection (Silero VAD)
        ├── speaker arbitration by cross-channel energy RATIO
        ├── anti-feedback gate (never re-translate our own output)
        ├── speech recognition  →  translation  →  speech synthesis
        └── hard-panned stereo output
                  │
                  └── AirPods (A2DP)
                        ├── Left bud  = Person A hears their language
                        └── Right bud = Person B hears their language
```

Both microphones arrive sample-synchronised through a single receiver, which is what makes the whole design work. **Speaker identity comes from the cross-channel energy ratio, never from absolute loudness:** a person's own chest microphone hears them 10–17 dB louder than the other person's microphone does, no matter how loudly either of them talks. Measured on this hardware: wearers separate at +10 to +18 dB, bleed sits at −15 to −17 dB.

## The model stack

| Stage | Model | Notes |
|---|---|---|
| Voice activity | Silero VAD | Per-channel, drives endpointing |
| Speech recognition (English) | Parakeet TDT 0.6B v3 int8 (sherpa-onnx) | RTF ~0.12, full punctuation |
| Speech recognition (zh/ja/ko) | SenseVoice-small int8 (sherpa-onnx) | RTF ~0.05 |
| Speech recognition (everything else) | faster-whisper `base` int8, **language pinned per channel** | RTF ~0.35–0.65 |
| Translation | opus-mt per-pair int8 (CTranslate2), NLLB-200-distilled-600M as universal fallback | opus ~0.3 s/sentence, NLLB ~2–3 s |
| Speech synthesis | Piper (medium voices), Supertonic-3 for some languages | RTF ~0.15 |

About 5 GB on disk, **~2.0 GB resident** with everything loaded and warm.

Why Parakeet is English-only: it runs its own internal language identification and the sherpa transducer API offers **no way to pin it**. On short or accented non-English speech it flips to English phonetics — a live session produced "bien" → "B N." and "¿cuánto cuestan?" → "Conto question." Whisper, which accepts an explicit language argument, costs about 1.5 seconds more per turn and never leaves the configured language. That trade is the single most important accuracy decision in the build.

## The screen

720×1280, portrait, sitting flat on a table between two people who face each other — so **one half is rotated 180°** and each person reads their own half right-side up. Each half shows one full-field state colour and one large word (idle / listening / translating / speaking / muted), the live transcript, a mute control, and a language picker with all 51 languages ordered by measured accuracy.

The transcript line is also a **cancel button**. Recognised text appears roughly 1.7 s after you stop speaking; audio reaches the other ear at ~2.5–4 s, so there is a real window to kill a mis-hear before the other person hears it. The pipeline never starts audio less than 1.0 s after the transcript is shown — cancellability is bought with a deliberate slice of the latency budget.

---

# 📊 Performance — the honest numbers

All measured on this hardware with real microphones, not estimated.

## Latency (end of speech → translated audio in the other ear)

| Path | Measured |
|---|---|
| English → Spanish, short dialogue turn (opus pair) | **~2.5 s** |
| Spanish → English (whisper ASR) | **~3–4 s** |
| Any pair falling back to NLLB | **3.5–5 s per sentence** |
| Long monologues | first audio while still speaking (chunks close every ~4 s) |
| Architectural floor as designed | **~2.0–2.2 s** |
| Power-on → ready on the glass | **46 s** |

The floor is mostly **deliberate policy, not slow code**: 0.7 s of endpoint silence, a ≥1.0 s cancel window, and merge holds. Compute is no longer the bottleneck for covered pairs. For comparison, human simultaneous interpreters run 2–4 s ear-to-voice.

## Language coverage

**51 languages** have a complete chain (recognition × translation × a voice). That number is set by the weakest stage — text-to-speech — because a language with no voice produces nothing in the earbud.

- **Bench-verified on this device (8):** Chinese, Japanese, English, Spanish, Portuguese, German, Russian, French. Korean is effectively a ninth.
- **The other 42 are estimates,** not measurements — published-benchmark error rates, shown in the picker so you can see what you are choosing.
- **The tail fails confidently.** Languages like Hindi, Bengali, Telugu, Marathi and Nepali sit at 41–55% word error rate, and whisper does not say "I didn't understand" — it produces fluent, wrong text. These stay available but are visibly marked.

---

# ⚠️ Limitations

The unedited list, from the project's own hardening audit.

**What is fragile.** Simultaneous speech works when both voices are strong on their own microphones; the weak side of an overlap in the −6..0 dB band is still discarded by design, and a bystander's voice can be decoded (even twice) instead of ignored — the mute button remains the only bystander defence. Accented speech on the non-English side costs real accuracy and ~1.5–2 s over the old (language-unstable) engine. The capture clock shows unexplained +5..+66 s excursions versus wall clock under I/O load — every pipeline decision rides the sample clock so nothing misbehaves, but the mechanism is not understood, only monitored. The DSI panel silently failed to probe on one boot (ribbon reseated; the supervisor now announces it). One AirPods sink means one shared hardware volume and SBC joint stereo — measured clean by ear, still a compromise. WAV evidence keeps only each session's first 8 turns.

**Not yet built or verified, and it matters.** No read-only root filesystem: a battery hard-cut can corrupt the SD card, and this device is a battery appliance — this is the top standing risk. EEPROM power flags unset (the DJI receiver runs inside the 600 mA USB cap — works, thin headroom). No RTC battery fitted, so fully-offline mode has no clock source. WiFi is still on. The UPS soak test under sustained ML load has not been run. The kiosk boot path was designed but not built (a desktop session hosts the UI). Unknown AirPods behaviours: one bud dying, true battery life. Known and tolerated bugs: the merge-hold deadline expires early, and the turn registry never prunes within a session.

**Two things the hardware cannot tell you.** A transmitter that is switched off is indistinguishable from a person who is not speaking — the on-screen level check is the manual test. And the language picker is a contract: speech in a language other than the one selected decodes as confident phonetic nonsense in the selected language.

---

# 🧠 Notable Engineering Problems

### Three clocks, and only one of them was right
Wall-clock time, the capture stream's sample counter, and the voice-activity library's *internal* counter all disagreed — the last drifted 38 seconds from the capture head over a nine-hour run. Mixing them produced failures that looked like anything but a timing bug: an anti-feedback gate silently comparing timestamps from different number lines and never matching, and a backlog meter claiming the pipeline was 40 seconds behind while audio arrived a second later. Everything timing-critical now uses the capture sample clock, converted at exactly one point.

### A dead thread that looked like a slow one
A punctuation-only transcript (`"."`, from a 0.4-second cough) hit a guard that tested one string and indexed another. The recognition thread died on the exception; every other thread kept running, so the screen showed "translating" indefinitely while twenty turns queued into a dead consumer. Every worker thread is now polled every five seconds — a dead one raises a named on-screen fault, is rebuilt in place with its queue migrated, and a second death within five minutes restarts the whole pipeline.

### Code that read as working for months
When someone's reply partly overlapped the device's own playback, the gate computed how much of the audio would survive trimming, logged the number, and then discarded the whole segment anyway — the "keep the rest" branch was never written. It looked correct in every log line it printed. Three barge-in replies were lost to it in a single session before the geometry was checked against the ledger by hand.

### A speech model that would not be told what language to expect
Parakeet runs its own internal language identification and the library exposes no way to pin it. On short or accented Spanish it flipped to English phonetics — "bien" became "B N.", "¿cuánto cuestan?" became "Conto question." — and the nonsense was then faithfully translated and spoken. Native-speaker test clips never showed it; only a real non-native speaker at the second microphone exposed it. It now handles English only, and every other language uses an engine that accepts an explicit language argument.

### A retry ladder nobody asked for
The recognition library silently retries at five temperatures with sampling whenever its own quality check fails — which accented audio triggers constantly. Measured: a 2.8× multiplier, up to 26 decoder passes for one short sentence, and combined with thread oversubscription it turned 1.6-second decodes into 18-second ones. Disabling it produced *better* text on every clip tested.

### The transmitter that lied
Worn microphone levels collapsed to −45 dBFS and transcripts turned to garbage. A day went into audio-path debugging on the assumption it was software, and a digital gain compensation was added. The real cause was a transient hardware state in the transmitters, cleared by power-cycling them — after which the added gain was actively harmful, pushing speech into clipping. The standing rule now: **if levels collapse, power-cycle the transmitters before touching anything in software.**

---

# 🚀 Getting Started

Full detail — including the parts that are easy to get wrong — is in **[docs/setup.md](docs/setup.md)**. The condensed version:

## 1. Clone and create the environment

```bash
git clone https://github.com/TobyM-engineering/Real-Time-Speech-Translator-V2.git
cd Real-Time-Speech-Translator-V2
python3 -m venv venv
venv/bin/pip install sherpa-onnx faster-whisper ctranslate2 piper-tts sentencepiece transformers numpy soundfile huggingface_hub smbus2 PySide6
```

## 2. Fetch the models (~5 GB)

```bash
tools/fetch_models.sh
```

## 3. Enable I²C for the battery gauge

```bash
sudo raspi-config nonint do_i2c 0
```

## 4. Add your device settings

```bash
cp device.example.json device.json
```

Fill in **your** receiver's node name and **your** earbuds' MAC address:

```bash
pw-cli ls Node | grep -i Rx     # the DJI receiver's node.name
bluetoothctl devices            # your earbuds' MAC
```

> ⚠️ `device.json` is listed in `.gitignore`. It holds hardware identifiers — never commit it.

## 5. Pair the earbuds (and keep the microphone closed)

```bash
bluetoothctl
power on
scan on
pair XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
connect XX:XX:XX:XX:XX:XX
```

**Never let anything open the earbuds' microphone.** If any process does, Bluetooth drops from A2DP to the headset profile and output collapses to 8 kHz mono for both people. The WirePlumber configuration in [docs/setup.md](docs/setup.md) removes the headset profile entirely, so it cannot happen by accident.

## 6. Run it

```bash
tools/run_pipeline.sh
```

Logs land in `logs/run_<timestamp>.log`. Expect READY in about 30 seconds cold.

## 7. Run automatically on boot

```bash
cp tools/translator.service ~/.config/systemd/user/    # edit the paths first
systemctl --user enable translator.service
loginctl enable-linger
```

Power-on to ready on the glass is about 46 seconds.

## 8. Check the hardware any time

```bash
venv/bin/python tools/doctor.py
```

Plain-language health check: microphone levels, channel separation, earbud state, temperature, memory.

---

# 📌 Repo Status

✅ Full offline loop working end to end — two people, two languages, two earbuds

✅ 51 languages with a complete chain; 8 bench-verified

✅ Speaker arbitration, anti-feedback gate, push-to-talk, tap-to-cancel

✅ Automatic recovery: Bluetooth ladder, capture reopen, worker-thread watchdog, pipeline restart

✅ Boots into the UI with no terminal and no login

🔧 Read-only root filesystem, EEPROM power flags, and kiosk boot still to do

🔧 Latency floor (~2 s) is policy-bound; clause-streamed translation is the next real win

---

# 📂 Repository Structure

```
Real-Time-Speech-Translator-V2/
│
├── src/                     the pipeline
│   ├── config.py            every threshold, each traced to a measurement
│   ├── capture.py           USB audio in
│   ├── frontend.py          VAD, chunking, arbitration, gate, deferral
│   ├── arbitration.py       who is speaking (cross-channel ratio)
│   ├── gate.py              anti-feedback ledger
│   ├── asr_worker.py        three-engine speech recognition
│   ├── mt_worker.py         translation (opus-mt + NLLB fallback)
│   ├── tts_worker.py        speech synthesis
│   ├── playback.py          hard-panned stereo mixer
│   ├── pipeline_core.py     Qt bridge, turn lifecycle, cancel
│   ├── supervisor.py        recovery ladders and fault monitoring
│   └── battery.py           UPS HAT fuel gauge
│
├── ui/                      QML interface + the language catalog
├── tools/                   setup, fetch, health check, benchmarks
├── tests/                   regression tests
├── docs/
│   ├── hardware.md          every part, a link, and why that part
│   ├── setup.md             the full build, start to finish
│   ├── how_it_works.md      signal path, models, measured latency
│   ├── recovery.md          fault detection and recovery layers
│   ├── power_and_thermal.md power path, battery, thermal limits
│   └── build_log.md         the narrative: what broke and why
├── hardware/                wiring diagrams
└── media/                   photos and demo video
```

## Documentation

| Document | What is in it |
|---|---|
| **[hardware.md](docs/hardware.md)** | Every part with a link and the reason it was chosen — including the three choices that are load-bearing |
| **[setup.md](docs/setup.md)** | Build guide from bare parts to autostart, with the traps that cost time |
| **[how_it_works.md](docs/how_it_works.md)** | Signal path, speaker separation, all three recognition engines, translation, per-stage measured latency |
| **[recovery.md](docs/recovery.md)** | What the device does when hardware or software fails, and what it cannot detect |
| **[power_and_thermal.md](docs/power_and_thermal.md)** | The power path, the battery gauge, thermal limits, and unfinished power work |
| **[build_log.md](docs/build_log.md)** | The narrative: what broke, what was wrongly believed, how it was resolved |

---

# 📄 License

MIT — see [LICENSE](LICENSE).
