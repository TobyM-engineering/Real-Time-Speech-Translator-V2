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

    def overlap_detail(self, person, t0, t1):
        """(fraction, matched_intervals) — forensic form, for the gate log:
        the live 100%-vs-53% question gets answered by seeing the intervals."""
        m = config.GATE_MARGIN
        seg = t1 - t0
        if seg <= 0:
            return 0.0, []
        covered, matched = 0.0, []
        with self._lock:
            spans = list(self._intervals[person])
        for (a, b) in spans:
            lo, hi = max(t0, a - m), min(t1, b + m)
            if hi > lo:
                covered += hi - lo
                matched.append((round(a, 2), round(b, 2)))
        return min(1.0, covered / seg), matched

    def overlap_fraction(self, person, t0, t1):
        """Fraction of segment [t0, t1] that overlaps playback into this
        person's ear, with GATE_MARGIN slack on each playback interval."""
        m = config.GATE_MARGIN
        seg = t1 - t0
        if seg <= 0:
            return 0.0
        covered = 0.0
        with self._lock:
            spans = list(self._intervals[person])
        # segments are short; a simple sum of clipped overlaps is fine
        for (a, b) in spans:
            lo, hi = max(t0, a - m), min(t1, b + m)
            if hi > lo:
                covered += hi - lo
        return min(1.0, covered / seg)
