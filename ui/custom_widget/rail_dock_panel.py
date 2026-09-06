"""Panel-rail side panel hard-docked to the rail (PS-style dock panel).

``ui/panel_rail.py::PanelRail`` launches these panels.  The panel is an
in-window child of the main window's central stack — it moves with the
window and stays above the canvas page.  It *opens* docked to the left of
the rail (on the canvas area, never covering the text-edit column).  Its
right edge and top are pinned to the rail, so the panel keeps a fixed,
predictable connection to its launcher icon; it is **not** freely
draggable.  The size is user-adjustable via invisible edge handles along
the left edge, bottom edge and bottom-left corner (no visible grip icon).  It re-anchors automatically when the host window is
resized or the rail moves, so it never drifts to a stale position.  The
resize floor comes from the content layout — the panel can not be
squashed into a single line of controls.  It never closes itself: only
the rail icon or the × button closes it.  The open state persists
through a ``pcfg`` attribute; the position is always reset to the rail
anchor.

Because it lives inside the main window, the app stylesheet applies
automatically — no per-instance theme refresh needed.
"""

from qtpy.QtCore import QEvent, QPoint, QSize, Qt, Signal
from qtpy.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class _EdgeHandle(QWidget):
    """隐形拖拽手柄：贴面板左缘/下缘/左下角，光标形状即提示（无可见图标）。

    必须是专用子控件——面板本体被 body 子控件完全覆盖，边缘鼠标事件到
    不了面板自身。背景经 QSS ``QWidget#RailDockEdge`` 钉为透明（全局
    QWidget 背景规则会给裸 QWidget 上底色）。
    """

    def __init__(self, parent, zone: str, cursor_shape):
        super().__init__(parent)
        self.setObjectName("RailDockEdge")
        self.zone = zone
        self.setCursor(cursor_shape)


class RailDockPanel(QFrame):
    """Canvas-area panel hard-docked to ``ui/panel_rail.py``.

    Right edge + top are pinned to the rail so the panel always sits at a
    predictable, connected location.  Resize-only (no free drag): invisible
    edge handles along the left edge, bottom edge and bottom-left corner
    (no visible grip icon — the cursor shape is the affordance), and
    host-resize / rail-move both trigger a re-anchor so the panel never
    drifts (PS-style dock).  The resize floor follows the content layout
    so controls never squash into a single line.
    """

    closed = Signal()
    HEADER_HEIGHT = 26
    ANCHOR_MARGIN = 8
    EDGE_MARGIN = 6
    CORNER_SIZE = 12

    def __init__(
        self,
        title: str,
        content_widget: QWidget,
        rail: QWidget,
        config_open: str,
    ):
        host = rail.window() if rail is not None else None
        super().__init__(getattr(host, "centralStackWidget", host))
        self._rail = rail
        self._config_open = config_open
        self._sized = False  # size set on first anchor, then preserved
        self._press_pos = None
        self._press_size = None
        self._press_zone = None
        self._header = None

        self.setMinimumSize(230, 140)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        # ── Header: title bar + close (not a drag handle) ──────────
        header = QWidget(self)
        header.setObjectName("RailDockHeader")
        header.setFixedHeight(self.HEADER_HEIGHT)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 0, 2, 0)
        header_layout.setSpacing(0)
        self._title_label = QLabel(title, header)
        self._title_label.setObjectName("RailDockTitle")
        close_btn = QToolButton(header)
        close_btn.setObjectName("RailDockCloseBtn")
        close_btn.setText("×")
        close_btn.setFixedSize(22, 22)
        close_btn.setToolTip(self.tr("Close"))
        close_btn.clicked.connect(self.close_panel)
        header_layout.addWidget(self._title_label, 1)
        header_layout.addWidget(close_btn)
        layout.addWidget(header)

        # ── Body ───────────────────────────────────────────────────
        body = QFrame(self)
        body.setObjectName("RailDockBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(8, 4, 8, 8)
        self._content = content_widget
        body_layout.addWidget(content_widget)
        # QLayout 计算 sizeHint 时忽略显式隐藏的子控件；内容组在
        # FontFormatPanel 里初始 hide() 过，这里必须显式恢复，否则
        # 浮层按空布局定尺寸（本体仍随浮层隐藏，不会提前露出）
        content_widget.show()
        layout.addWidget(body, 1)

        # ── Invisible edge handles (left / bottom / bottom-left) ───
        self._handles = {
            "left": _EdgeHandle(self, "left", Qt.CursorShape.SizeHorCursor),
            "bottom": _EdgeHandle(self, "bottom", Qt.CursorShape.SizeVerCursor),
            "left-bottom": _EdgeHandle(
                self, "left-bottom", Qt.CursorShape.SizeBDiagCursor
            ),
        }
        for handle in self._handles.values():
            handle.installEventFilter(self)
        self._layout_handles()
        self._header = header

        # 宿主缩放 / 窄栏移动时自动重锚（位置补偿，保持硬连接）
        parent = self.parentWidget()
        if parent is not None:
            parent.installEventFilter(self)
        if self._rail is not None:
            self._rail.installEventFilter(self)

        self.hide()

    # ── public API ───────────────────────────────────────────────

    def set_title(self, title: str) -> None:
        self._title_label.setText(title)

    def open_panel(self) -> None:
        self._anchor_to_rail()
        self.show()
        self.raise_()
        self._set_open_state(True)

    def close_panel(self) -> None:
        self.hide()
        self._set_open_state(False)
        self.closed.emit()

    def toggle(self) -> None:
        if self.isVisible():
            self.close_panel()
        else:
            self.open_panel()

    def hide_keep_state(self) -> None:
        """Hide without clearing the open-state flag (owner switched away;
        the panel should reappear when the page returns)."""
        self.hide()

    def is_open(self) -> bool:
        return self.isVisible()

    # ── internals ────────────────────────────────────────────────

    def _set_open_state(self, open_state: bool) -> None:
        from utils.config import pcfg

        setattr(pcfg, self._config_open, open_state)

    def _anchor_to_rail(self) -> None:
        """Pin the panel to the rail: right edge/lane + top, resized to fit.

        On the first open the size is derived from the content sizeHint;
        afterwards the user's chosen size is kept.  The position is always
        reset to the rail anchor so the panel stays predictably connected
        to its launcher icon (PS-style dock, not a free float).  Also
        called from the event filter when the host resizes or the rail
        moves (position compensation).
        """
        if not self._sized:
            hint = self.sizeHint()
            size = QSize(
                min(max(hint.width(), self.minimumWidth()), 460),
                min(max(hint.height(), self.minimumHeight()), 640),
            )
            self._sized = True
            self.resize(size)
        if self._rail is not None and self.parentWidget() is not None:
            tl = self._rail.mapTo(self.parentWidget(), QPoint(0, 0))
            pos = QPoint(tl.x() - self.ANCHOR_MARGIN - self.width(), tl.y())
        else:
            pos = QPoint(self.ANCHOR_MARGIN, self.ANCHOR_MARGIN)
        self.move(self._clamp_pos(pos))

    def _clamp_pos(self, pos: QPoint) -> QPoint:
        host = self.parentWidget()
        if host is None:
            return pos
        return QPoint(
            max(0, min(pos.x(), host.width() - 60)),
            max(0, min(pos.y(), host.height() - 24)),
        )

    def _min_size(self) -> QSize:
        """Resize floor: the content layout's minimum — never squashed.

        Even a floating panel keeps its controls usable; the floor is the
        larger of the explicit minimum and what the content needs, so a
        too-small drag cannot compress the items into a single line.
        """
        layout_min = QSize(0, 0)
        layout = self.layout()
        if layout is not None:
            layout_min = layout.minimumSize()
        return QSize(
            max(self.minimumSize().width(), layout_min.width()),
            max(self.minimumSize().height(), layout_min.height()),
        )

    def _clamp_size(self, size: QSize) -> QSize:
        host = self.parentWidget()
        floor = self._min_size()
        w = max(floor.width(), size.width())
        h = max(floor.height(), size.height())
        if host is not None:
            # 右缘锚定窄栏后向左/下生长：宽度受左边界（x>=0）约束，
            # 高度受宿主底边约束（顶部固定锚点）。
            right_edge = self.x() + self.width()
            w = min(w, max(right_edge, floor.width()))
            h = min(h, max(host.height() - self.y(), floor.height()))
        return QSize(w, h)

    def eventFilter(self, obj, event) -> bool:
        et = event.type()
        # 宿主缩放 / 窄栏移动 → 自动重锚（位置补偿）：面板不会错位，
        # 无需重新打开刷新位置
        if self._sized and self.isVisible():
            if obj is self.parentWidget() and et == QEvent.Type.Resize:
                self._anchor_to_rail()
            elif obj is self._rail and et == QEvent.Type.Move:
                self._anchor_to_rail()
        if isinstance(obj, _EdgeHandle) and self._handles.get(obj.zone) is obj:
            if (
                et == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self._press_pos = event.globalPosition().toPoint()
                self._press_size = self.size()
                self._press_zone = obj.zone
                return True
            if (
                et == QEvent.Type.MouseMove
                and self._press_pos is not None
            ):
                delta = event.globalPosition().toPoint() - self._press_pos
                # 自由侧（左/下）跟光标：向左/下拖 = 变大（右缘+顶部锚定，
                # resizeEvent 里 _anchor_to_rail 保持右缘钉住窄栏）
                w, h = self._press_size.width(), self._press_size.height()
                if "left" in self._press_zone:
                    w -= delta.x()
                if "bottom" in self._press_zone:
                    h += delta.y()
                self.resize(self._clamp_size(QSize(w, h)))
                return True
            if (
                et == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
                and self._press_pos is not None
            ):
                self._press_pos = None
                self._press_size = None
                self._press_zone = None
                return True
        return super().eventFilter(obj, event)

    def _layout_handles(self) -> None:
        """隐形手柄贴边重摆（随面板尺寸变化）；左下角手柄盖住两缘交叠处。"""
        w, h = self.width(), self.height()
        self._handles["left"].setGeometry(0, 0, self.EDGE_MARGIN, h)
        self._handles["bottom"].setGeometry(
            0, h - self.EDGE_MARGIN, w, self.EDGE_MARGIN
        )
        self._handles["left-bottom"].setGeometry(
            0, h - self.CORNER_SIZE, self.CORNER_SIZE, self.CORNER_SIZE
        )
        for handle in self._handles.values():
            handle.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_handles()
        # 每次尺寸变化都回到窄栏锚点，保持与图标硬连接（右缘+顶部固定）
        if self._sized:
            self._anchor_to_rail()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_panel()
            event.accept()
            return
        super().keyPressEvent(event)
