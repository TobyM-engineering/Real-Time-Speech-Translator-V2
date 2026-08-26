#!/usr/bin/env python3
"""Launch the Signal prototype fullscreen on the panel (Wayland)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "wayland")
os.environ.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")

import json

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

base = os.path.dirname(os.path.abspath(__file__))
app = QGuiApplication(sys.argv)
engine = QQmlApplicationEngine()
with open(os.path.join(base, "languages.json")) as f:
    engine.rootContext().setContextProperty("langCatalog", json.load(f))
engine.load(os.path.join(base, "signal_proto.qml"))
if not engine.rootObjects():
    sys.exit("QML failed to load")
sys.exit(app.exec())
