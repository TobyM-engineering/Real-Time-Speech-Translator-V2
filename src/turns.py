"""Turn registry (design doc): every accepted utterance becomes a turn with a
monotonic id. Cancel is a flag checked at every stage boundary; stage 1 only
creates turns and records their fate."""
import threading
from dataclasses import dataclass, field


@dataclass
class Turn:
    turn_id: int
    person: str
    t0: float
    t1: float
    state: str = "captured"     # captured → transcribed → translated → spoken
    cancelled: bool = False
    closed: bool = False        # exactly-one terminal event guard (pending count)
    forced_split: bool = False  # segment ended at the 10 s cap, not real silence
    notes: list = field(default_factory=list)


class TurnRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._turns = {}
        self._next = 1

    def new_turn(self, person, t0, t1):
        with self._lock:
            t = Turn(self._next, person, t0, t1)
            self._turns[t.turn_id] = t
            self._next += 1
            return t

    def cancel(self, turn_id):
        with self._lock:
            t = self._turns.get(turn_id)
            if t:
                t.cancelled = True

    def get(self, turn_id):
        with self._lock:
            return self._turns.get(turn_id)

    def snapshot(self):
        with self._lock:
            return list(self._turns.values())
