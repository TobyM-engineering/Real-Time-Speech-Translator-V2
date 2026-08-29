# Power and Thermal Design

A translator you carry to a conversation has to run on a battery, and four A76 cores running inference continuously is a real thermal load. Both are measured here, and both have unfinished work stated at the end.

---

## The power path

```
mains
  └── RasTech 27 W GaN PD supply (5.1 V / 5 A)
        └── UPS HAT (E) USB-C input          ← mains enters HERE, not at the Pi
              ├── charges the 4 × 21700 pack
              └── powers the Pi through the HAT
                    └── Pi 5  →  display (GPIO pins 2 and 6)
                              →  DJI receiver (black USB 2.0 port)
```

Once the HAT is fitted, **the Pi's own USB-C jack goes unused.** Everything arrives through the HAT, which is what allows the device to be unplugged mid-conversation and keep running.

<img src="diagrams/physical-stack.svg" alt="The assembled stack layer by layer with the power path" width="100%">

### Why 5.1 V / 5 A specifically

In the normal path the HAT does two jobs at once: carry the entire system load *and* charge a 4-cell pack. **16–19 W with peaks past 21 W is an expected figure, not a measured one** — the UPS soak test that would establish it has not been run (see [Known-unfinished power work](#known-unfinished-power-work)). On that expectation a typical 15–18 W phone charger would leave nothing for charging — it would stall, or the rail would sag under load — so the 27 W supply was chosen to cover both.

Before the HAT and cells were installed, the same adapter powered the Pi directly through its USB-C jack, and it still works that way for bench use without the battery.

---

## The battery, and knowing when to stop

4 × 21700 cells in series. Measured fully charged: **16.49 V at the pack, ≈4.12 V per cell.**

The reason for choosing a HAT with a fuel gauge rather than a plain power bank is that the device can *know* its state instead of guessing. The gauge sits on I²C at address `0x2d`, and the supervisor polls it every 10 seconds for pack voltage, current, state of charge, and all four individual cell voltages.

**The low-battery rule watches individual cells, not the pack average:** the warning fires when any single cell drops below **3150 mV** while discharging, and clears above 3300 mV or as soon as charging starts. That is Waveshare's own cutoff, and their firmware force-shuts-down roughly 60 seconds afterwards. Surfacing it as a visible warning well ahead of that is the difference between a controlled stop and an unclean power loss on a device with an SD card mounted read-write.

A pack average would hide exactly the failure that matters — one weak cell collapsing while the other three look healthy.

On the screen this is a coloured pip beside the ready indicator, with the percentage and charge state in the detail panel. The gauge being unreachable is treated as "no battery fitted": the indicator simply hides, so the software runs unchanged on a mains-only build.

### Verified on battery

`throttled=0x0` across a full battery session — the 5 V rail stayed clean under sustained inference, with no undervoltage flagged.

---

## Thermal

The Pi 5 soft-caps at **80 °C** and throttles at **85 °C**, dropping 2.4 GHz to 1.5 GHz. That is a **37% clock cut applied to every stage of the pipeline**, and it arrives precisely when the device is being worked hardest. Every latency figure in this repository assumes no throttling; under throttle, add roughly a third to all of them.

**The Active Cooler is therefore not optional**, and it uses thermal pads only — no paste.

Measured: **48–53 °C idle** with the cooler fitted, under a load that includes the model stack resident and Bluetooth audio streaming. The supervisor polls temperature every 10 seconds and raises a visible warning at 80 °C, clearing below 78 °C.

Thermal headroom is also why the decode threads are deliberately under-subscribed. Recognition engines run 2 threads each rather than 3, so a burst uses 4 threads on 4 cores instead of 6 — measured to cut a contended worst case from 17.95 s to 7.82 s, and idling costs only 0–2% on one engine and 8–11% on the other.

---

## Known-unfinished power work

Stated rather than glossed, because these are real and this section would be dishonest without them.

- **No read-only root filesystem.** This is the top standing risk in the project. A battery device *will* eventually be hard-cut, and a read-write root on an SD card is exactly the wrong thing to have when that happens. The high-endurance card reduces the damage; it does not remove it.
- **EEPROM flags unset.** `PSU_MAX_CURRENT=5000` and `POWER_OFF_ON_HALT=1` are both still at defaults. Because power arrives over the HAT rather than a negotiating USB-C connection, the Pi boots restricting all USB ports to 600 mA total (`usb_max_current_enable=0` measured). The DJI receiver works inside that cap today, but the headroom is thin and it is not a designed margin.
- **No RTC battery fitted.** The part is on hand. Until it is in, a fully offline device boots with no idea what time it is.
- **WiFi is still on.** An offline-mode switch is unbuilt. The 2.4 GHz radio shares an antenna with Bluetooth, so this is a plausible source of audio dropouts under load — and it is the exact failure class that killed v1's USB Bluetooth dongle.
- **The UPS soak test has not been run.** Sustained inference on battery to depletion, watching for voltage sag on the pogo-pin contact path with `vcgencmd pmic_read_adc`, remains an open test. Real battery life is therefore **unmeasured** — 16–19 W against a 4×21700 pack suggests a few hours, but that is arithmetic, not a measurement, and this project does not publish arithmetic as data.
