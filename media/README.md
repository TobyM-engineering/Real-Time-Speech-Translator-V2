# Media

Photographs and video of the finished device. Every file here is referenced from the documentation, so dropping a file in with the name below is the whole job — the markup and captions are already written at the matching point in each document.

## Photographs

| File | Shows | Referenced from |
|---|---|---|
| `device_ready.jpg` | The hero shot: the device powered on, one half showing READY and the other LISTO, with both DJI transmitters and both AirPods in frame | [`../README.md`](../README.md), above the fold |
| `stack_side.jpg` | Side-on view of the board stack — display on its standoffs, Pi 5 with the active cooler, UPS HAT, the four 21700 cells, and the DJI receiver | [`../hardware/hardware.md`](../hardware/hardware.md) |
| `wiring_diagram.png` | The full wiring: display ribbon to `CAM/DISP 0`, display power on GPIO pins 2 and 6, fan header, receiver on a black USB 2.0 port, mains into the UPS HAT's USB-C input | [`../hardware/hardware.md`](../hardware/hardware.md) |

## Video

| File | Shows | Referenced from |
|---|---|---|
| `demo_translation.mp4` | English to Spanish, spoken live, with the transcript and translation on screen | [`../README.md`](../README.md), the Demo section |
| `demo_language_change.mp4` | Changing the language pair on the touchscreen, then speaking English to French | [`../ui/README.md`](../ui/README.md), the language picker |
| `demo_cancel.mp4` | Starting a turn and cancelling it before it plays | [`../ui/README.md`](../ui/README.md), the cancel window |
| `demo_french.mp4` | An English to French turn | [`../software/how_it_works.md`](../software/how_it_works.md), translation |
| `demo_levels_battery.mp4` | The centre button: battery percentage and live microphone levels in dB | [`../software/recovery.md`](../software/recovery.md), monitoring |

## Two things about the video

**The translated audio is dubbed.** In every recording the translation played into an earbud, which the phone camera could not hear, so the audio was added afterwards from the exact text the device produced. Everything else — the speech, the screen, the timing — is live.

**Encode as H.264, not HEVC.** H.264 MP4 plays inline on GitHub; HEVC does not, and the file silently becomes a download link instead of a player.

## Formats

JPEG for photographs, PNG for diagrams, MP4 for video. Anything beyond the seven files above is free-form.

The SVG figures used throughout the documentation are generated rather than photographed, and live in [`../software/diagrams/`](../software/diagrams/), [`../hardware/diagrams/`](../hardware/diagrams/) and [`../ui/`](../ui/).
