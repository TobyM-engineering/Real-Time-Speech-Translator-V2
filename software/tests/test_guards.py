"""Guards from the 2026-08-28 hardening audit: every place model output
is indexed must survive an empty result, and the monitors must announce
their own failure modes.

Run:  venv/bin/python -m unittest tests.test_guards
"""
import os
import json
import sys
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
REPO = os.path.dirname(ROOT)          # repository root, above software/
from src.mt_worker import MtWorker
from src.pipeline_core import Bridge


class _R:
    def __init__(self, hyp, scores=None):
        self.hypotheses = hyp
        self.scores = scores or []


def make_bridge():
    with open(f"{REPO}/ui/languages.json") as f:
        catalog = json.load(f)
    b = Bridge(catalog, downstream=False)
    b.logs = []
    b.logMsg.connect(b.logs.append)
    return b


class MtEmptyHypothesis(unittest.TestCase):
    """The engines have never returned an empty hypothesis live — but if
    one ever does, the MT thread must flag the sentence, not die (the
    2026-08-28 ASR-thread death was exactly this class)."""

    def _worker(self):
        return MtWorker(on_log=lambda m: None, on_translated=lambda *a: None)

    def test_opus_batch(self):
        w = self._worker()
        sp_in = types.SimpleNamespace(encode=lambda s, out_type: ["x"])
        sp_out = types.SimpleNamespace(decode=lambda h: "nope")
        tr = types.SimpleNamespace(
            translate_batch=lambda t, **kw: [_R([])])
        text, ratio, per = w._opus_batch((sp_in, sp_out, tr), ["hola"])[0]
        self.assertEqual(text, "")
        self.assertGreaterEqual(ratio, 99.0)   # detector must discard it

    def test_nllb_batch(self):
        w = self._worker()
        w._tok = types.SimpleNamespace(
            src_lang=None,
            encode=lambda s: [1],
            convert_ids_to_tokens=lambda ids: ["x"],
            convert_tokens_to_ids=lambda t: [1],
            decode=lambda ids, skip_special_tokens: "nope")
        w._tr = types.SimpleNamespace(
            translate_batch=lambda t, **kw: [_R([])])
        text, ratio, per = w._nllb_batch(["hola"], "spa_Latn", "eng_Latn")[0]
        self.assertEqual(text, "")
        self.assertGreaterEqual(ratio, 99.0)

    def test_score(self):
        w = self._worker()
        out, ratio, per, dig_ok = w._score("hola", ["x"], _R([]))
        self.assertEqual(out, "")
        self.assertGreaterEqual(ratio, 99.0)


class WatchdogCoverage(unittest.TestCase):
    def test_frontend_is_monitored_and_escalates(self):
        b = make_bridge()
        self.assertIn("frontend", b.worker_threads())
        # no in-place rebuild exists: restart must refuse, which makes the
        # supervisor escalate to a pipeline restart instead of pretending
        self.assertFalse(b.restart_worker("frontend"))

    def test_dead_supervisor_is_announced(self):
        b = make_bridge()
        b._lag_for = lambda p: 0.0
        b.supervisor = types.SimpleNamespace(is_alive=lambda: False)
        b._poll_backlog()
        self.assertTrue(any("supervisor thread is DEAD" in m
                            for m in b.logs))
        self.assertIn("restart the device", b._fault)


class DiskCheck(unittest.TestCase):
    def test_low_disk_raises_pill_and_recovers(self):
        from src.supervisor import Supervisor
        b = make_bridge()
        sup = Supervisor(b)
        import os
        real = os.statvfs
        os.statvfs = lambda p: types.SimpleNamespace(
            f_bavail=100, f_frsize=1_000_000)          # 100 MB free
        try:
            sup._check_disk(time.time())
            self.assertEqual(b._fault, "Storage almost full")
            os.statvfs = lambda p: types.SimpleNamespace(
                f_bavail=2000, f_frsize=1_000_000)     # 2 GB free
            sup._disk_next = 0.0
            sup._check_disk(time.time())
            self.assertEqual(b._fault, "")
        finally:
            os.statvfs = real


if __name__ == "__main__":
    unittest.main()
