"""Worker-liveness watchdog harness (2026-08-28): build the REAL Bridge
(all models, fake silent capture), kill the ASR worker thread live, and
verify the supervisor detects the death, raises the fault pill, rebuilds
the worker (queue migrated), clears the fault, and decodes again. Then
kill it a second time inside WORKER_RESTART_WINDOW_S and verify the
escalation to pipeline restart fires (exec intercepted, not executed).
BT recovery is stubbed out so the ladder doesn't fight the test."""
import os
import sys
import threading
import time
import wave

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import src.frontend as F
from src import config


class FakeCap:
    """Silent stereo capture, runs until stopped."""
    def __init__(self, cb):
        self.error = None
        self._cb = cb
        self._alive = True

    def start(self):
        blk = np.zeros((config.CHUNK, 2), dtype=np.int16).tobytes()

        def feed():
            i = 0
            while self._alive:
                self._cb(blk, i)
                i += config.CHUNK
                time.sleep(0.01)
        threading.Thread(target=feed, daemon=True).start()

    def stop(self):
        self._alive = False

    def is_alive(self):
        return self._alive


F.CaptureThread = FakeCap

from src import supervisor as S
S.Supervisor._check_bt = lambda self, now: None   # test isolation

import json
from PySide6.QtCore import QCoreApplication
from src.pipeline_core import Bridge

app = QCoreApplication(sys.argv)
catalog = json.load(open("ui/languages.json"))
logs = []


def wait_for(pred, timeout, why):
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.05)
    print(f"TIMEOUT waiting for: {why}")
    return False


bridge = Bridge(catalog, downstream=True)
bridge.logMsg.connect(lambda m: (logs.append(m), print("  ", m, flush=True))
                      if any(k in m for k in ("FAULT", "SUP", "READY", "ASR"))
                      else logs.append(m))
bridge.start()
assert wait_for(lambda: bridge._ready_emitted, 90, "READY")

w = wave.open("tools/bench/clips/bench_es_short.wav", "rb")
es = (np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
      .astype(np.float32) / 32768.0)
w.close()


def kill_asr():
    bridge.asr._decode = None            # next decode raises TypeError
    t = bridge.frontend.registry.new_turn(config.PERSON_B, 1.0, 2.0)
    bridge.asr.submit(t, np.zeros(config.SR, dtype=np.float32))


print("\n== kill 1: sabotage ASR, expect detect + rebuild + recover ==")
kill_asr()
assert wait_for(lambda: not bridge.asr.is_alive() or
                any("worker thread 'asr' is DEAD" in m for m in logs),
                15, "death detected")
assert wait_for(lambda: any("worker 'asr' RESTARTED" in m for m in logs),
                60, "worker restarted")
assert wait_for(lambda: bridge.asr.is_alive(), 5, "new thread alive")

t = bridge.frontend.registry.new_turn(config.PERSON_B, 3.0, 6.6)
bridge.asr.submit(t, es)
assert wait_for(lambda: any("necesito comprar" in m for m in logs),
                30, "post-restart decode works")
print("  post-restart decode OK")

print("\n== kill 2 (inside restart window): expect PIPELINE RESTART ==")
fired = []
bridge.supervisor._exec_restart = lambda: fired.append(1)
kill_asr()
assert wait_for(lambda: any("escalating to PIPELINE RESTART" in m
                            for m in logs), 20, "escalation logged")
assert wait_for(lambda: fired, 10, "exec-restart invoked (intercepted)")
print("\nALL WATCHDOG CHECKS PASSED")
