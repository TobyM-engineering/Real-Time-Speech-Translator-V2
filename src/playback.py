"""T5 playback mixer: the single self-mixed stereo stream (D3, spiked).
LEFT = Person A's ear, RIGHT = B's. Owns the ≥1.0 s hold rule, per-ear gain,
the 1.03x backlog drain, and writes the anti-feedback gate ledger. Blocking
20 ms writes into a shrunken pipe are the clock (~100 ms write-to-sink)."""
import fcntl
import os
import subprocess
import threading
import time
from collections import deque

import numpy as np

from src import config

_F_SETPIPE_SZ = 1031


class _Ear:
    def __init__(self):
        self.queue = deque()
        self.current = None
        self.pos = 0
        self.active = False
        self.last_end = None       # wall time the previous chunk finished
        self.wait_cause = "first"  # why we sat idle since then


class Playback(threading.Thread):
    def __init__(self, on_log, ledger, epoch, on_play_start, on_ear_active):
        """epoch: wall time of capture start — the ledger speaks capture-stream
        seconds. on_play_start(turn) fires as a turn's first sample is written;
        on_ear_active(person, bool) tracks the listener SPEAKING state."""
        super().__init__(name="playback", daemon=True)
        self.on_log = on_log
        self.ledger = ledger
        self.epoch = epoch
        self.on_play_start = on_play_start
        self.on_ear_active = on_ear_active
        self._lock = threading.Lock()
        self._ears = {config.PERSON_A: _Ear(), config.PERSON_B: _Ear()}
        self._stopping = threading.Event()
        self.error = None

        self.last_write_wall = None   # stall detector: supervisor watches this

        # gap tone: two soft descending notes (~250 ms) — "something didn't
        # come through", deliberately not an alert chime
        sr = config.OUT_RATE
        def note(freq, dur):
            t = np.arange(int(dur * sr)) / sr
            x = np.sin(2 * np.pi * freq * t).astype(np.float32)
            f = int(0.010 * sr)
            x[:f] *= np.linspace(0, 1, f, dtype=np.float32)
            x[-f:] *= np.linspace(1, 0, f, dtype=np.float32)
            return x
        self._gap_tone = (np.concatenate([
            note(660, 0.11), np.zeros(int(0.02 * sr), dtype=np.float32),
            note(520, 0.11)]) * config.GAP_TONE_LEVEL)

    def enqueue_gap(self, ear):
        """Queue the gap tone into this ear — a turnless signal item: no
        state changes, no cancel semantics, but it DOES write the gate
        ledger like any audio into an ear."""
        with self._lock:
            self._ears[ear].queue.append(
                {"turn": None, "samples": self._gap_tone,
                 "not_before": 0.0, "enqueued_at": time.time()})

    def enqueue(self, ear, turn, samples, not_before):
        with self._lock:
            self._ears[ear].queue.append(
                {"turn": turn, "samples": samples, "not_before": not_before,
                 "enqueued_at": time.time()})

    def take_fresh_items(self, max_age_s=10.0):
        """For BT recovery: hand surviving (recent, uncancelled) queued items
        to a replacement Playback.

        THE ONE EXCEPTION TO D5 (Toby-approved, 2026-08-26): queued TRANSLATED
        audio older than 10 s is discarded here — stale speech delivered a
        minute late is worse than a gap. Scope: this recovery path only.
        Nothing is ever dropped before translation; normal operation never
        drops anything."""
        now = time.time()
        out = {}
        with self._lock:
            for ear, e in self._ears.items():
                keep = [i for i in e.queue
                        if now - i["enqueued_at"] <= max_age_s
                        and (i["turn"] is None or not i["turn"].cancelled)]
                dropped = len(e.queue) - len(keep)
                if dropped:
                    self.on_log(f"PLAY dropped {dropped} stale queued item(s) "
                                f"for ear {ear} on recovery")
                out[ear] = keep
                e.queue.clear()
        return out

    def adopt_items(self, items):
        with self._lock:
            for ear, lst in items.items():
                self._ears[ear].queue.extend(lst)

    def backlog_seconds(self, ear):
        with self._lock:
            e = self._ears[ear]
            n = sum(len(i["samples"]) for i in e.queue)
            if e.current is not None:
                n += max(0, len(e.current["samples"]) - e.pos)
        return n / config.OUT_RATE

    def stop(self):
        self._stopping.set()

    # ------------------------------------------------------------------
    def run(self):
        try:
            env = dict(os.environ)
            env.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
            proc = subprocess.Popen(
                ["pw-play", "--raw", "--rate", str(config.OUT_RATE),
                 "--channels", "2", "--format", "s16",
                 "--latency", config.PLAY_LATENCY,
                 "--target", config.AIRPODS_NODE, "-"],
                env=env, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            sz = fcntl.fcntl(proc.stdin.fileno(), _F_SETPIPE_SZ,
                             config.PIPE_BYTES)
            self.on_log(f"PLAY stream up (pipe {sz} B ≈ "
                        f"{sz/(config.OUT_RATE*4)*1000:.0f} ms)")
            F = config.OUT_FRAME
            chan = {config.PERSON_A: 0, config.PERSON_B: 1}
            while not self._stopping.is_set():
                out = np.zeros((F, 2), dtype=np.float32)
                now = time.time()
                for person, ch in chan.items():
                    e = self._ears[person]
                    if e.current is None:
                        with self._lock:
                            if not e.queue:
                                e.wait_cause = ("audio-not-ready(design)"
                                                if e.last_end else e.wait_cause)
                            while e.queue:
                                head = e.queue[0]
                                if head["turn"] is not None and \
                                        head["turn"].cancelled:
                                    e.queue.popleft()
                                    self.on_log(f"PLAY turn#"
                                                f"{head['turn'].turn_id} "
                                                f"cancelled before play")
                                    continue
                                if now < head["not_before"]:
                                    e.wait_cause = "hold-rule"
                                    break
                                e.current = e.queue.popleft()
                                e.pos = 0
                                break
                        if e.current is not None:
                            gap = (now - e.last_end) if e.last_end else 0.0
                            cause = e.wait_cause if gap > 0.05 else "seamless"
                            e.wait_cause = "tick"
                            if e.current["turn"] is None:
                                self.on_log(f"PLAY ear={person} gap tone")
                            else:
                                self.on_log(f"PLAY ear={person} turn#"
                                            f"{e.current['turn'].turn_id} "
                                            f"start gap={gap:.2f}s "
                                            f"cause={cause}")
                                e.current["turn"].state = "playing"
                                self.on_play_start(e.current["turn"])
                            if not e.active:
                                e.active = True
                                self.on_ear_active(person, True)
                    if e.current is not None and \
                            e.current["turn"] is not None and \
                            e.current["turn"].cancelled and \
                            not e.current.get("fading"):
                        # cancel cuts the SOUNDING chunk too (2026-08-27):
                        # play a 50 ms fade-out from the current position,
                        # then this item ends; the pop loop skips the rest
                        s = e.current["samples"]
                        k = int(0.05 * config.OUT_RATE)
                        end = min(len(s), e.pos + k)
                        tail = s[e.pos:end].copy()
                        if len(tail):
                            tail *= np.linspace(1.0, 0.0, len(tail),
                                                dtype=np.float32)
                        e.current = {**e.current,
                                     "samples": np.concatenate([s[:e.pos],
                                                                tail]),
                                     "fading": True}
                        self.on_log(f"PLAY ear={person} turn#"
                                    f"{e.current['turn'].turn_id} CUT "
                                    f"mid-chunk ({len(tail)/config.OUT_RATE*1000:.0f} ms fade)")
                    if e.current is not None:
                        backlog = self.backlog_seconds(person)
                        speed = (config.DRAIN_SPEED
                                 if backlog > config.DRAIN_BACKLOG_S else 1.0)
                        k = int(F * speed)
                        src = e.current["samples"][e.pos:e.pos + k]
                        e.pos += k
                        if e.pos >= len(e.current["samples"]):
                            e.current = None
                            e.last_end = now + F / config.OUT_RATE
                        if len(src) < k:
                            src = np.pad(src, (0, k - len(src)))
                        if speed != 1.0:
                            idx = np.linspace(0, k - 1, F)
                            src = np.interp(idx, np.arange(k), src)
                        out[:, ch] = src[:F] * config.EAR_GAIN[person]
                        s = now - self.epoch
                        self.ledger.add(person, s, s + F / config.OUT_RATE)
                    elif e.active and not e.queue:
                        e.active = False
                        self.on_ear_active(person, False)
                pcm = np.clip(out, -1, 1)
                proc.stdin.write((pcm * 32767).astype(np.int16).tobytes())
                self.last_write_wall = time.time()
            proc.stdin.close()
            proc.terminate()
        except Exception as exc:
            self.error = exc
            if not self._stopping.is_set():
                self.on_log(f"PLAY FATAL: {exc} — audio out is down "
                            f"(recovery is stage 4)")
