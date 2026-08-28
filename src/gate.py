"""Anti-feedback gate ledger (design doc: written by playback T5, read by
capture T1 at segment accept). All times are capture-stream seconds, never
wall clock. Stage 1 has no playback thread, so intervals arrive only from the
--fake-playback test hook; the query path is the real one."""
import threading

from src import config


class GateLedger:
    def __init__(self):
        self._lock = threading.Lock()
        self._intervals = {config.PERSON_A: [], config.PERSON_B: []}

    def add(self, person, t0, t1):
        """Record that audio played into `person`'s ear over [t0, t1].
        Coalesces contiguous 20 ms mixer ticks into single intervals and
        prunes entries older than 5 minutes."""
        with self._lock:
            iv = self._intervals[person]
            if iv and t0 <= iv[-1][1] + 0.05:
                iv[-1] = (iv[-1][0], max(iv[-1][1], t1))
            else:
                iv.append((t0, t1))
            if len(iv) > 500:
                del iv[:100]

    def _covered(self, person, t0, t1):
        """Coverage of [t0, t1] by margin-extended playback intervals,
        MERGED before summing. The 2026-08-26 bug was summing unmerged
        extensions: a fragmented ledger's ±margin islands overlap each
        other and double-count (live: printed 84% where geometry was 22%)."""
        m = config.GATE_MARGIN
        with self._lock:
            spans = sorted(self._intervals[person])
        merged = []
        for a, b in spans:
            a, b = a - m, b + m
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        covered, matched = 0.0, []
        for a, b in merged:
            lo, hi = max(t0, a), min(t1, b)
            if hi > lo:
                covered += hi - lo
                matched.append((round(a + m, 2), round(b - m, 2)))
        return covered, matched

    def overlap_detail(self, person, t0, t1):
        """(fraction, matched merged intervals) — forensic form for the
        gate log."""
        seg = t1 - t0
        if seg <= 0:
            return 0.0, []
        covered, matched = self._covered(person, t0, t1)
        return min(1.0, covered / seg), matched

    def overlap_fraction(self, person, t0, t1):
        """Fraction of segment [t0, t1] covered by playback into this
        person's ear (margin-extended, merged)."""
        seg = t1 - t0
        if seg <= 0:
            return 0.0
        covered, _ = self._covered(person, t0, t1)
        return min(1.0, covered / seg)
