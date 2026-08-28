"""Curated interjection table: short conversational utterances bypass NLLB
entirely — measured, every one-word input draws the model's subtitle-dialog
prior (see CLAUDE.md 2026-08-27). Deterministic table in, deterministic
speech out.

The data lives in interjections_data.json, built by
tools/build_interjections.py from en.wiktionary.org translation tables —
every cell cited to its source page, judgement calls marked, uncovered
language/word combinations listed in the file's meta. Recognition is
derived from ALL cited candidate forms per language (any of them spoken →
the concept), plus the hand-curated legacy variants. An uncovered target
falls back to normal MT — logged by the MT worker, never silent.
"""
import json
import os
import re

_STRIP = re.compile(r"[\s.,!?¡¿。、！？…؟।\"'\-–—]+")


def _norm(text):
    return _STRIP.sub(" ", text.casefold()).strip()


# forms whose standalone meaning flips with context in their own language —
# excluded from RECOGNITION only (rendering them is still fine)
_RECOG_EXCLUDE = {("es", "bueno"), ("fr", "bon")}   # discourse "well," markers

_DATA = json.load(open(os.path.join(os.path.dirname(__file__),
                                    "interjections_data.json"),
                       encoding="utf-8"))

RENDER = {c: {code: cell["text"] for code, cell in d["langs"].items()}
          for c, d in _DATA["concepts"].items()}

RECOGNIZE = {}
for _c, _d in _DATA["concepts"].items():
    for _code, _cell in _d["langs"].items():
        _m = RECOGNIZE.setdefault(_code, {})
        for _form in _cell["recognize"] + [_cell["text"]]:
            _key = _norm(_form)
            if _key and (_code, _key) not in _RECOG_EXCLUDE \
                    and _key not in _m:   # first concept wins, order stable
                _m[_key] = _c
for _code, _table in _DATA.get("extra_recognize", {}).items():
    _m = RECOGNIZE.setdefault(_code, {})
    for _form, _c in _table.items():
        _m[_norm(_form)] = _c             # legacy hand variants override

UNCOVERED = _DATA.get("uncovered", [])
SKIPPED = _DATA.get("skipped_by_rule", {})


def match(sentence, src_code):
    """Concept key if this sentence is a known interjection, else None."""
    table = RECOGNIZE.get(src_code)
    if not table:
        return None
    return table.get(_norm(sentence))


def render(concept, tgt_code):
    """Curated target-language form, or None if this target is uncovered."""
    return RENDER.get(concept, {}).get(tgt_code)
