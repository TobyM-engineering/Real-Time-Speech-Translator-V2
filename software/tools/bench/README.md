# Bench Scripts and Ground-Truth Clips

Measurement scripts and reference audio from the benchmarking sessions. Every performance number quoted in the documentation was produced here.

Run them from the repository root, with the virtual environment's Python:

```bash
venv/bin/python software/tools/bench/bench_parakeet.py
```

## Harnesses

| Script | What it exercises |
|---|---|
| `bench_parakeet.py` | Recognition speed and accuracy, engine against engine, on the clips below |
| `measure_candidates.py` | Candidate engines on real captured audio |
| `svoice_en_ab.py` | SenseVoice against whisper for English |
| `profile_es_fr.py` | Where the time goes in a Spanish and a French turn |
| `chunk_harness.py` | Mid-speech chunk closing — where the cuts land, and whether they land between words |
| `trim_harness.py` | The anti-feedback trim: real audio, fake playback over part of it, decoding what survives |
| `overlap_harness.py` | Simultaneous speech — deferral, the release rule, and the queue cap |
| `watchdog_harness.py` | Kills a live worker thread and checks the supervisor detects, rebuilds, and escalates |

The harnesses stream real audio through the real pipeline classes with a fake capture source, so they test the shipped code rather than a copy of it.

## Clips

`clips/` holds eight ground-truth recordings in Spanish, French, German and Portuguese, plus one regression specimen. `clips/bench_meta.json` maps every clip to its exact spoken text, its language and its duration.

`clips/parakeet_empty_regression.wav` is a live microphone recording that Parakeet decodes as an empty string, deterministically, while SenseVoice reads it correctly. It is the specimen behind the empty-decode fallback in `../../src/asr_worker.py` — keep it even if a future model version decodes it, because it still exercises that path.

`../piper_voices.json` is the input `../build_language_catalog.py` needs to generate the language catalog.
