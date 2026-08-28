Measurement scripts and ground-truth clips preserved from the 2026-08-26/27 bench sessions (the session scratchpad is tmpfs and does not survive shutdown). Hard-coded paths reference that scratchpad — adjust before rerunning. bench_meta.json maps each clip to its exact spoken reference text. tools/piper_voices.json is the required input for tools/build_language_catalog.py.

parakeet_empty_regression.wav (2026-08-28): live mic clip that parakeet deterministically decodes as EMPTY while SenseVoice reads it fine — the specimen behind asr_worker._decode_fallback. Details in bench_meta.json.
