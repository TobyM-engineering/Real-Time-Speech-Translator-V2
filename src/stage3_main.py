#!/usr/bin/env python3
"""Stage 3: the full offline loop — capture → arbitration → gate → ASR →
NLLB → TTS → the AirPods, with the Signal UI live. First real audio out.

Run on the panel:  venv/bin/python -m src.stage3_main
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "wayland")
os.environ.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from src.pipeline_core import Bridge

ROOT = "<REPO-ROOT>"


def main():
    app = QGuiApplication(sys.argv)
    with open(f"{ROOT}/ui/languages.json") as f:
        catalog = json.load(f)
    bridge = Bridge(catalog, downstream=True)
    bridge.logMsg.connect(lambda m: print(m, flush=True))

    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("bridge", bridge)
    ctx.setContextProperty("langCatalog", catalog)
    engine.load(f"{ROOT}/ui/signal_live.qml")
    if not engine.rootObjects():
        sys.exit("QML failed to load")
    bridge.start()
    rc = app.exec()
    bridge.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())
