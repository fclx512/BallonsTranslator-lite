"""Anchor-button drop-down float panel (interaction mirrors RailDockPanel).

Parents to the main window's central widget so the panel can stretch over
the canvas instead of being clipped to the launcher sidebar: its left edge
pins just outside the sidebar's right edge (the sidebar stays visible and
its controls interactive) and it sizes itself generously from the content
hint.  Close via the anchor toggle, × or Esc.  No ``pcfg`` open-state
memory and no resize grip (the content is expected to scroll internally).
QSS reuses the ``RailDock*`` objectNames and the float-panel rule so it
looks identical to the canvas rail panels.
"""

from qtpy.QtCore import QEvent, QPoint, Qt, Signal
from qtpy.QtWidgets import QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from .widget import Widget


class FloatDropPanel(Widget):
    closed = Signal()
    HEADER_HEIGHT = 26
    MARGIN = 6
    MIN_HEIGHT = 480
    MIN_WIDTH = 420

    def __init__(
        self,
        title: str,
        content_widget: QWidget,
        anchor: QWidget,
        edge: QWidget = None,
        parent: QWidget = None,
    ):
        # 宿主默认取主窗口中央控件（锚点与画布的共同祖先），浮层不被
        # 锚点所在窄栏裁切；*edge* 是左缘钉靠的参照控件（锚点所在侧栏）
        window = anchor.window()
        host = parent
        if host is None:
            central = getattr(window, "centralWidget", lambda: None)()
            host = central or window
        super().__init__(host)
        self._anchor = anchor
        self._edge = edge if edge is not None else anchor.parentWidget()
        self._content = content_widget

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

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

        body = QWidget(self)
        body.setObjectName("RailDockBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(8, 4, 8, 8)
        body_layout.addWidget(content_widget)
        layout.addWidget(body, 1)

        host.installEventFilter(self)
        self.hide()

    # ── public API ───────────────────────────────────────────────

    def set_title(self, title: str) -> None:
        self._title_label.setText(title)

    def open_panel(self) -> None:
        self._ensure_host()
        self._place()
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _ensure_host(self) -> None:
        """打开时惰性解析宿主：构造期控件树往往尚未挂进主窗口，
        ``window()``/``centralWidget`` 那时不可靠，须在首次打开时重钉。"""
        window = self._anchor.window()
        central = getattr(window, "centralWidget", lambda: None)()
        host = central or window
        if self.parentWidget() is host:
            return
        old = self.parentWidget()
        if old is not None:
            old.removeEventFilter(self)
        self.setParent(host)
        host.installEventFilter(self)

    def close_panel(self) -> None:
        if not self.isVisible():
            return
        self.hide()
        self.closed.emit()

    def toggle(self) -> None:
        if self.isVisible():
            self.close_panel()
        else:
            self.open_panel()

    # ── internals ────────────────────────────────────────────────

    def _place(self) -> None:
        """左缘钉在侧栏右缘外侧、顶随锚点按钮，尺寸按内容给足。"""
        host = self.parentWidget()
        if host is None:
            return
        anchor_tl = self._anchor.mapTo(host, QPoint(0, 0))
        y = anchor_tl.y() + self._anchor.height() + 4
        edge_tl = self._edge.mapTo(host, QPoint(0, 0))
        left = edge_tl.x() + self._edge.width() + 4
        # 可用宽不足时收缩，保底 160（宿主几何未就绪的测试环境避免负宽）
        avail_w = max(host.width() - left - self.MARGIN, 160)
        w = max(self.MIN_WIDTH, self.sizeHint().width())
        w = min(w, avail_w)
        avail_h = max(host.height() - y - self.MARGIN, self.MIN_HEIGHT)
        h = min(max(self.MIN_HEIGHT, self.sizeHint().height()), avail_h)
        self.setGeometry(left, y, w, h)

    def eventFilter(self, obj, event) -> bool:
        if (
            obj is self.parentWidget()
            and event.type() == QEvent.Type.Resize
            and self.isVisible()
        ):
            self._place()
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.close_panel()
            event.accept()
            return
        super().keyPressEvent(event)
