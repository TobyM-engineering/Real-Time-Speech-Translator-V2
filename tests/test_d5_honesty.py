"""D5 honesty tests (2026-08-28): the pause-request must not lie about
the cause, and must not latch forever.

Run:  venv/bin/python -m unittest tests.test_d5_honesty
Logic-level: Bridge without start() (no models, no threads, no Qt loop),
_lag_for stubbed, _poll_backlog driven by hand.
"""
import json
import sys
import time
import unittest

sys.path.insert(0, "<REPO-ROOT>")
from src import config
from src.pipeline_core import Bridge


def make_bridge():
    with open("<REPO-ROOT>/ui/languages.json") as f:
        catalog = json.load(f)
    b = Bridge(catalog, downstream=False)
    b._lag_for = lambda p: b.test_lag
    b.logs = []
    b.logMsg.connect(b.logs.append)
    return b


class D5Honesty(unittest.TestCase):
    def test_fault_suppresses_pause_and_resumes(self):
        b = make_bridge()
        b.test_lag = 40.0                       # over BACKLOG_HARD_S
        b._poll_backlog()
        self.assertEqual(b._backlog_level["A"], 2)   # honest HARD
        b.set_fault("Earbuds not connected — test")
        b._poll_backlog()
        self.assertEqual(b._backlog_level["A"], 0)   # pill governs now
        self.assertTrue(any("pause-request suppressed" in m for m in b.logs))
        b.set_fault("")
        b._poll_backlog()
        self.assertEqual(b._backlog_level["A"], 2)   # backlog still real
        self.assertTrue(any("pause-request resumes" in m for m in b.logs))

    def test_unexplained_hard_escalates_after_timeout(self):
        b = make_bridge()
        b.test_lag = 40.0
        old = config.D5_STUCK_S
        config.D5_STUCK_S = 0.3
        try:
            b._poll_backlog()                    # arms _d5_hard_since
            self.assertFalse(b.stall_escalate)
            time.sleep(0.35)
            b._poll_backlog()
            self.assertTrue(b.stall_escalate)
            self.assertTrue(any("D5 stuck" in m for m in b.logs))
            self.assertEqual(b._fault, "Translation stalled — restarting…")
        finally:
            config.D5_STUCK_S = old

    def test_no_escalation_while_fault_explains(self):
        """Earbuds in the case must NOT exec-loop the pipeline."""
        b = make_bridge()
        b.test_lag = 40.0
        b.set_fault("Earbuds not connected — test")
        old = config.D5_STUCK_S
        config.D5_STUCK_S = 0.3
        try:
            b._poll_backlog()
            time.sleep(0.35)
            b._poll_backlog()
            self.assertFalse(b.stall_escalate)
        finally:
            config.D5_STUCK_S = old

    def test_supervisor_picks_up_stall_flag(self):
        from src.supervisor import Supervisor
        b = make_bridge()
        sup = Supervisor(b)
        fired = []
        sup._pipeline_restart = lambda reason: fired.append(reason)
        b.stall_escalate = True
        sup._check_stall(time.time())
        self.assertTrue(fired and "stalled" in fired[0])
        self.assertFalse(b.stall_escalate)


if __name__ == "__main__":
    unittest.main()
