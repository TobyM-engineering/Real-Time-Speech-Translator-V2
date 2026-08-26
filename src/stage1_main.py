#!/usr/bin/env python3
"""Stage 1 bench tool: the shared AudioFrontend (capture → VAD → cap →
arbitration → gate) with log-only output. Verified on hardware 2026-08-26;
stage 2 runs the same frontend code with ASR and the UI attached.

  venv/bin/python -m src.stage1_main --duration 120 --fake-playback A:30:40
"""
import argparse
import sys
import time

from src import config
from src.frontend import AudioFrontend


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=120.0)
    ap.add_argument("--fake-playback", action="append", default=[],
                    metavar="P:T0:T1")
    ap.add_argument("--mute-a", action="store_true")
    ap.add_argument("--mute-b", action="store_true")
    args = ap.parse_args()

    t0 = time.time()

    def log(msg):
        print(f"[{time.time()-t0:7.2f}] {msg}", flush=True)

    fe = AudioFrontend(on_log=log)
    windows = []
    for spec in args.fake_playback:
        p, a, b = spec.split(":")
        fe.ledger.add(p.upper(), float(a), float(b))
        windows.append({"p": p.upper(), "a": float(a), "b": float(b),
                        "opened": False, "closed": False})
        log(f"FAKE  playback into {p.upper()}'s ear over stream {a}-{b}s")
    if args.mute_a:
        fe.set_muted(config.PERSON_A, True)
    if args.mute_b:
        fe.set_muted(config.PERSON_B, True)

    fe.start()
    log(f"READY capturing from DJI at {config.SR} Hz "
        f"(A=left/black windscreen, B=right/grey)")
    deadline = t0 + args.duration
    try:
        while time.time() < deadline and not fe.error:
            time.sleep(0.2)
            now = time.time() - t0   # stream ≈ wall at realtime capture
            for w in windows:
                if not w["opened"] and now >= w["a"]:
                    w["opened"] = True
                    log(f"GATE-TEST window OPEN into {w['p']}'s ear — speak "
                        f"into TX{'1' if w['p'] == 'A' else '2'} NOW "
                        f"(until {w['b']:.0f}s)")
                if not w["closed"] and now >= w["b"]:
                    w["closed"] = True
                    log(f"GATE-TEST window CLOSED for {w['p']}")
    except KeyboardInterrupt:
        log("interrupted")
    finally:
        fe.stop()
    log("stage 1 run complete")


if __name__ == "__main__":
    sys.exit(main())
