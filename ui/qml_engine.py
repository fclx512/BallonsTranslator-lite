"""Singleton QML engine and QQuickWidget factory.

Usage: call ``init_qml_engine(app)`` once after QApplication is created,
then use ``create_curtain(parent, qml_source)`` to build curtain widgets.
"""

from __future__ import annotations

import os.path as osp
from typing import TYPE_CHECKING

from qtpy.QtCore import Qt, QUrl
from qtpy.QtGui import QColor
from qtpy.QtWidgets import QWidget

from utils import shared

if TYPE_CHECKING:
    from PyQt6.QtQuickWidgets import QQuickWidget

QQmlEngine = None
_QML_ROOT: str | None = None
_bridge = None  # QmlBridge singleton


def _qml_dir() -> str:
    return osp.join(shared.PROGRAM_PATH, "ui", "qml")


def init_qml_engine(app) -> None:
    """Initialise the shared QQmlEngine and QML bridge.

    Must be called after QApplication is created, before any curtain is used.
    No-op in headless mode.
    """
    global QQmlEngine, _QML_ROOT, _bridge

    if shared.HEADLESS:
        return

    from PyQt6.QtQml import QQmlEngine as _Engine

    QQmlEngine = _Engine

    # Import bridge and register on root context
    from ui.qml_bridge import QmlBridge

    _bridge = QmlBridge()
    _bridge.setObjectName("pyBridge")

    _QML_ROOT = _qml_dir()


def get_bridge() -> "QmlBridge | None":
    """Return the shared QmlBridge singleton, or None before init."""
    return _bridge


def create_curtain(parent: QWidget, qml_source: str) -> "QQuickWidget":
    """Create a transparent QQuickWidget loaded with *qml_source*.

    *qml_source* is a path relative to ``ui/qml/``, e.g.
    ``"overlays/ConfigCurtain.qml"``.

    Returns a hidden QQuickWidget parented to *parent*.  The caller is
    responsible for positioning, resizing, and showing/hiding.
    """
    if _bridge is None:
        raise RuntimeError("QML engine not initialised — call init_qml_engine() first")

    from PyQt6.QtQuickWidgets import QQuickWidget

    curtain = QQuickWidget(parent)

    curtain.setAttribute(Qt.WA_TranslucentBackground)
    curtain.setClearColor(QColor(0, 0, 0, 0))
    curtain.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)

    engine = curtain.engine()
    if engine is None:
        # QQuickWidget creates its own engine; ensure import path
        engine = QQmlEngine()
    engine.setImportPathList(
        engine.importPathList() + [_QML_ROOT]
    )

    root_ctx = curtain.rootContext()
    root_ctx.setContextProperty("pyBridge", _bridge)

    qml_path = osp.join(_QML_ROOT, qml_source)
    curtain.setSource(QUrl.fromLocalFile(qml_path))
    curtain.hide()

    return curtain
