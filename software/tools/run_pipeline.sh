#!/bin/bash
# Canonical pipeline launcher — the supported way to start the translator.
#
# Logs land in logs/run_<timestamp>.log on the SD card, NOT tmpfs: the
# 2026-08-28 power cycle destroyed every earlier session log and with it
# the evidence for when the parakeet empty-transcript fault first appeared.
# Diagnostic history outranks SD wear (high-endurance card, ~0.1–0.3 MB/h
# of text). Revisit when the read-only-rootfs build lands.
#
# Run from the repository root with software/ on PYTHONPATH, so the
# package stays "src" and no import inside the pipeline has to change.
#
# WAV dumps stay on tmpfs (/tmp/translator_dumps): the first 8 turns of
# the session, not history. Promote a keeper into the repo by hand,
# e.g. tools/bench/clips/parakeet_empty_regression.wav.

cd "$(dirname "$0")/../.." || exit 1   # repository root

if pgrep -f "python.*stage3_main" > /dev/null; then
    echo "pipeline already running (PID $(pgrep -f 'python.*stage3_main' | head -1)) — not starting a second one"
    exit 1
fi

mkdir -p logs /tmp/translator_dumps
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export TXV2_DUMP_DIR="${TXV2_DUMP_DIR:-/tmp/translator_dumps}"

LOG="logs/run_$(date +%Y%m%d_%H%M%S).log"
if [ "$1" = "--foreground" ]; then
    # systemd service mode (2026-08-28 autostart): stay attached so
    # Restart=on-failure sees crashes; same log path either way. The
    # supervisor's execv escalation keeps the PID, so systemd keeps
    # tracking across an in-place pipeline restart.
    echo "started (foreground) — logging to $LOG"
    PYTHONPATH="$PWD/software" exec venv/bin/python -m src.stage3_main >> "$LOG" 2>&1 < /dev/null
fi
PYTHONPATH="$PWD/software" setsid -f venv/bin/python -m src.stage3_main >> "$LOG" 2>&1 < /dev/null
echo "started — logging to $LOG"
echo "watch with: tail -f $LOG"
