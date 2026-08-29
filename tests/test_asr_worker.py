"""Regression tests for the ASR worker's pure helpers.

Run:  venv/bin/python -m unittest tests.test_asr_worker
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from src.asr_worker import _last_content_word


class DanglingWordGuard(unittest.TestCase):
    def test_punctuation_only_never_raises(self):
        """2026-08-28: whisper-fr returned '.' for a 0.4 s blip; the old
        inline expression raised IndexError and killed the ASR thread —
        21 turns then queued into a dead consumer and D5 latched HARD
        for good. These inputs must return '' forever."""
        for text in (".", "…", " ", "", ". . .", ",", " ; : "):
            self.assertEqual(_last_content_word(text), "", repr(text))

    def test_real_transcripts(self):
        self.assertEqual(_last_content_word("I went to the store and"),
                         "and")
        self.assertEqual(_last_content_word("Hello."), "hello")
        self.assertEqual(_last_content_word("Say it better."), "better")
        self.assertEqual(_last_content_word("Un momento."), "momento")


if __name__ == "__main__":
    unittest.main()
