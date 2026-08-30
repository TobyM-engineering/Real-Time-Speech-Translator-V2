# Media

Photographs and video of the finished device. Every file here is referenced from the documentation, so dropping a file in with the name below is the whole job — the markup and captions are already written at the matching point in each document.

## Photographs

| File | Shows | Referenced from |
|---|---|---|
| `device_ready.jpg` | The hero shot: the device powered on, one half showing READY and the other LISTO, with both DJI transmitters and both AirPods in frame | [`../README.md`](../README.md), above the fold |
| `stack_side.jpg` | Side-on view of the board stack — display on its standoffs, Pi 5 with the active cooler, UPS HAT, the four 21700 cells, and the DJI receiver | [`../hardware/hardware.md`](../hardware/hardware.md) |

## Diagrams

| File | Shows | Used for |
|---|---|---|
| `system-architecture.svg` / `.png` | The whole flow in one picture: two mics, one receiver, the Pi running all three models, out to one pair of earbuds — inside a boundary marked *all of this runs on the device*, with the cloud API drawn outside it and the link to it cut | Sharing outside the repository. The PNG is 2800 × 1700 because LinkedIn cannot display SVG. |

## Video

| File | Shows | Referenced from |
|---|---|---|
| `demo_translation.mp4` | English to Spanish, spoken live, with the transcript and translation on screen | [`../README.md`](../README.md), the Demo section |
| `demo_language_change.mp4` | Changing the language pair on the touchscreen, then speaking English to French | [`../screen/README.md`](../screen/README.md), the language picker |
| `demo_cancel.mp4` | Starting a turn and cancelling it before it plays | [`../screen/README.md`](../screen/README.md), the cancel window |
| `demo_french.mp4` | An English to French turn | [`../software/how_it_works.md`](../software/how_it_works.md), translation |
| `demo_levels_battery.mp4` | The centre button: battery percentage and live microphone levels in dB | [`../software/recovery.md`](../software/recovery.md), monitoring |

## About the video

**The translated audio is dubbed.** In every recording the translation played into an earbud, which the camera could not hear, so the audio was added afterwards from the exact text the device produced. Everything else — the speech, the screen, the timing — is live.

### Build note — export H.264, never HEVC

A reminder to myself before uploading, because the failure is silent: the file appears in the repository, looks fine, and simply refuses to play for whoever opens it.

**Phones default to HEVC.** On iPhone that is Settings → Camera → Formats → *High Efficiency*; **switch it to *Most Compatible*, or transcode after the fact.** HEVC in an MP4 will not decode in Firefox at all, and is patchy elsewhere depending on the machine. H.264 with AAC audio plays everywhere — every browser, every editor, and GitHub's own player.

To check a file before committing:

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 media/demo_translation.mp4
```

`h264` is what you want. If it says `hevc`, transcode it:

```bash
ffmpeg -i input.mov -c:v libx264 -crf 20 -preset slow -c:a aac -movflags +faststart media/demo_translation.mp4
```

**One thing the codec cannot fix.** A `.mp4` referenced by a repository path renders as a *link* on github.com, not an inline player — GitHub strips `<video>` tags from Markdown. Inline players come from files uploaded through GitHub's own editor, which rewrites them to a `user-attachments` URL. If a video playing in place matters, drag the file into the README editor on github.com once and paste the URL it gives over the repository path. The paths as written work for anyone who clones.

## Formats

JPEG for photographs, PNG for diagrams, MP4 for video. Anything beyond the seven files above is free-form.

The SVG figures used throughout the documentation are generated rather than photographed, and live in [`../software/diagrams/`](../software/diagrams/), [`../hardware/diagrams/`](../hardware/diagrams/) — including the connection diagram — and [`../screen/`](../screen/).
