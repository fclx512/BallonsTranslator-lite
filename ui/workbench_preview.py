"""工作台审批预览浮层（D44）：滚轮缩放 + 拖拽平移，独立于任务页。

**为什么搬出来**：预览原先是任务页里固定 120~280px 高的一小块（D23 定的
「列表下方展开、100% 原比例、只滚动不缩放」）。实测截图 433×395px 就看不全，
而工作台的纵向空间本来就被候选列表与执行行分掉了。现改为**工作台之外的浮层**：
in-window child of 主窗口中央区（与 ``ui/custom_widget/rail_dock_panel.py::RailDockPanel``
同款做法——跟着窗口走、盖在画布页上，不占工作台宽度），在中央区内**自由拖动**
（拖标题条），四边／四角可拉伸。

**D23 的口径不变**：默认就是 **100% 原比例**，不自动缩到适应窗口——缩不缩
由用户显式决定。**尺寸随图自适应**（上限 ``MAX_SIZE``）：单气泡截图 134×43
不该泡在 620×520 的空底里白占画布。

交互（它是个**临时浮层**，不设关闭按钮——关它有三条路）：

- 滚轮＝以光标为锚点缩放（0.1x ~ 8x）
- 左键拖拽＝平移（内容比视口小时自动居中，拖不出边界）
- 双击＝适应窗口；标题条两个按钮＝「适应窗口」／「1:1」
- **点画布（宿主区域）＝关闭**、**Esc＝关闭**——两者都不吞事件（返回
  ``False``），画布自己的 Esc 语义不因浮层开着而失效
- 换任务／重规划＝自动关闭（旧图对应的行可能已经不存在了）
- 手动拖过标题条或拉伸过，尺寸就交给用户（``_auto_size=False``）

**职责边界**：面板只负责显示。截图与叠加框都出自任务层
（``ui/workbench_tasks.py::BatchTask.preview``），由
``ui/workbench_batch_view.py::BatchTaskView`` 在选中行时喂进来（100% 原比例，
界面层不做缩放）。
"""

import logging

from qtpy.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from qtpy.QtGui import QMouseEvent, QPainter
from qtpy.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("workbench_preview")

# 缩放上下限与每格滚轮的步进（1.25 倍：4 格≈2.4 倍，手感不跳）
MIN_SCALE = 0.1
MAX_SCALE = 8.0
WHEEL_STEP = 1.25


class _PreviewCanvas(QWidget):
    """图的缩放/平移视图：默认 1:1，滚轮缩放、左键拖拽、双击适应窗口。"""

    scale_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        # objectName 的 QSS 底色对裸 QWidget 不生效，须显式开（仓库既有做法）
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("WorkbenchPreviewCanvas")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._pixmap = None
        self._scale = 1.0
        self._offset = QPoint(0, 0)  # 内容左上角在视口坐标里的位置
        self._pan_from = None  # (按下点, 按下时的 offset)
        self._hint = QLabel(self)
        self._hint.setObjectName("WorkbenchPreviewEmptyHint")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setWordWrap(True)

    # ── 内容 ────────────────────────────────────────────────────

    def set_content(self, pixmap, caption: str = ""):
        """换图：**每次回到 100%**（D23 的审批口径），大图从中心看起。"""
        usable = pixmap is not None and not pixmap.isNull()
        self._pixmap = pixmap if usable else None
        self._scale = 1.0
        self._hint.setText("" if usable else (caption or self.tr("Nothing to preview.")))
        self._hint.setVisible(not usable)
        self._center_content()
        self.update()
        self.scale_changed.emit(self._scale)

    def clear(self):
        self._pixmap = None
        self._hint.setText("")
        self._hint.setVisible(True)
        self._scale = 1.0
        self.update()

    def scale(self) -> float:
        return self._scale

    def pixmap_size(self) -> QSize:
        """当前图的**原始像素**尺寸（没有图时 0×0）——浮层据此自适应大小。"""
        return QSize(0, 0) if self._pixmap is None else self._pixmap.size()

    def fit(self):
        """适应窗口（小图不放大——审批图放大没有信息量，上限就是 100%）。"""
        if self._pixmap is None:
            return
        area = self._viewport()
        if area.isEmpty():
            return
        scale = min(
            area.width() / max(1, self._pixmap.width()),
            area.height() / max(1, self._pixmap.height()),
            1.0,
        )
        self._apply_scale(max(MIN_SCALE, scale))
        self._center_content()
        self.update()

    def actual_size(self):
        """回到 100% 原比例（D23 的默认口径）。"""
        self._apply_scale(1.0)
        self._center_content()
        self.update()

    # ── 几何 ────────────────────────────────────────────────────

    def _viewport(self) -> QRect:
        return self.rect().adjusted(1, 1, -1, -1)

    def _content_size(self) -> QSize:
        if self._pixmap is None:
            return QSize(0, 0)
        return QSize(
            int(round(self._pixmap.width() * self._scale)),
            int(round(self._pixmap.height() * self._scale)),
        )

    def _content_rect(self) -> QRect:
        return QRect(self._offset, self._content_size())

    def _center_content(self):
        area = self._viewport()
        size = self._content_size()
        self._offset = QPoint(
            area.x() + max(0, (area.width() - size.width()) // 2),
            area.y() + max(0, (area.height() - size.height()) // 2),
        )
        self._clamp_offset()

    def _clamp_offset(self):
        """内容小于视口时居中；大于视口时不许拖出边界（防拖飞）。"""
        area = self._viewport()
        size = self._content_size()
        x, y = self._offset.x(), self._offset.y()
        if size.width() <= area.width():
            x = area.x() + (area.width() - size.width()) // 2
        else:
            x = max(area.right() - size.width() + 1, min(x, area.x()))
        if size.height() <= area.height():
            y = area.y() + (area.height() - size.height()) // 2
        else:
            y = max(area.bottom() - size.height() + 1, min(y, area.y()))
        self._offset = QPoint(x, y)

    def _apply_scale(self, scale: float):
        scale = max(MIN_SCALE, min(MAX_SCALE, scale))
        if abs(scale - self._scale) < 1e-6:
            return
        self._scale = scale
        self._clamp_offset()
        self.update()
        self.scale_changed.emit(self._scale)

    # ── 事件 ────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._hint.setGeometry(self._viewport())
        self._clamp_offset()

    def wheelEvent(self, event):
        if self._pixmap is None:
            return
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return
        # 以光标为锚点：先取光标下的内容坐标，缩放后让同一点回到光标处
        anchor = event.position().toPoint()
        content_x = (anchor.x() - self._offset.x()) / self._scale
        content_y = (anchor.y() - self._offset.y()) / self._scale
        self._apply_scale(self._scale * (WHEEL_STEP**steps))
        self._offset = QPoint(
            int(round(anchor.x() - content_x * self._scale)),
            int(round(anchor.y() - content_y * self._scale)),
        )
        self._clamp_offset()
        self.update()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._pixmap is not None:
            self._pan_from = (event.position().toPoint(), QPoint(self._offset))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event):
        if self._pan_from is None:
            return
        press, offset = self._pan_from
        self._offset = offset + (event.position().toPoint() - press)
        self._clamp_offset()
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._pan_from is not None and event.button() == Qt.MouseButton.LeftButton:
            self._pan_from = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if self._pixmap is not None:
            self.fit()
            event.accept()

    def paintEvent(self, event):
        if self._pixmap is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(self._content_rect(), self._pixmap)


class _ElidedTitle(QLabel):
    """标题条文字：窄面板里按省略号截断，不把标题条撑宽。

    标题说清"在看哪一页的哪个框"（图尺寸对审阅没有意义），但页面名可能很长；
    不给它 ``minimumWidth`` 的话，``minimumSizeHint`` 就是整段文字的宽度，
    浮层的自适应下界会被它顶到很远。下限取"够读出页名 + 块号"的宽度——
    ``_fit_to_content`` 的宽度地板就是由它算出来的。
    """

    MIN_VISIBLE_WIDTH = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setMinimumWidth(self.MIN_VISIBLE_WIDTH)

    def setText(self, text: str):
        self._full_text = text or ""
        self._apply_elide()

    def natural_width(self, cap: int) -> int:
        """标题想要多宽（上限 ``cap``）：浮层据此留出装得下标题的宽度。

        短标题（"002.jpg · 块 0"）应当整条可见，长页面名才截断。
        """
        return min(cap, self.fontMetrics().horizontalAdvance(self._full_text))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self):
        metrics = self.fontMetrics()
        elided = metrics.elidedText(
            self._full_text,
            Qt.TextElideMode.ElideRight,
            max(0, self.width()),
        )
        super().setText(elided)


class _ResizeHandle(QWidget):
    """隐形拉伸手柄：光标形状即提示（对齐 RailDockPanel 的做法）。"""

    _CURSORS = {
        "left": Qt.CursorShape.SizeHorCursor,
        "right": Qt.CursorShape.SizeHorCursor,
        "bottom": Qt.CursorShape.SizeVerCursor,
        "top-left": Qt.CursorShape.SizeFDiagCursor,
        "bottom-right": Qt.CursorShape.SizeFDiagCursor,
        "top-right": Qt.CursorShape.SizeBDiagCursor,
        "bottom-left": Qt.CursorShape.SizeBDiagCursor,
    }

    def __init__(self, parent, zone: str):
        super().__init__(parent)
        self.setObjectName("WorkbenchPreviewEdge")
        self.zone = zone
        self.setCursor(self._CURSORS.get(zone, Qt.CursorShape.ArrowCursor))


class WorkbenchPreviewPanel(QFrame):
    """审批预览浮层（行为与职责见模块 docstring）。"""

    closed = Signal()

    HEADER_HEIGHT = 28
    ANCHOR_MARGIN = 16
    EDGE_MARGIN = 5
    CORNER_SIZE = 14
    # 自适应尺寸的上下界：下界＝标题条放得下（按钮与缩放读数不能被挤没），
    # 上界＝再大就白白盖住画布（大图靠滚轮缩放看，不靠铺满窗口）
    MIN_SIZE = QSize(220, 120)
    MAX_SIZE = QSize(620, 520)
    # 图片四周留的呼吸边（画布自己是 1px 视口内缩 + 1px 浮层边框）
    CONTENT_MARGIN = 8
    # 标题最多占的宽度（页面名很长时按省略号截断，浮层不跟着变宽）
    TITLE_MAX_WIDTH = 200
    # 宽度地板的余量：尺寸提示在 QSS 生效（polish）后会比首次测量略宽，
    # 不留几像素余量，标题就会差几个像素被截成"…"。
    TITLE_SLACK = 8

    def __init__(self, host: QWidget, parent=None):
        super().__init__(host if parent is None else parent)
        self.setObjectName("WorkbenchPreviewPanel")
        self.setMinimumSize(self.MIN_SIZE)
        self._placed = False
        self._auto_size = True  # 用户拖过标题条／拉伸过就交给用户
        self._filter_installed = False
        self._press_pos = None
        self._press_geom = None
        self._drag_origin = QPoint(0, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        # ── 标题条（拖动即移动浮层）────────────────────────────
        header = QWidget(self)
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header.setObjectName("WorkbenchPreviewHeader")
        header.setFixedHeight(self.HEADER_HEIGHT)
        header.setCursor(Qt.CursorShape.SizeAllCursor)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(8, 0, 2, 0)
        header_lay.setSpacing(4)
        self._title = _ElidedTitle(header)
        self._title.setObjectName("WorkbenchPreviewTitle")
        self._zoom_label = QLabel("100%", header)
        self._zoom_label.setObjectName("WorkbenchPreviewZoom")
        self.fit_btn = QToolButton(header)
        self.fit_btn.setObjectName("WorkbenchPreviewToolBtn")
        self.fit_btn.setText(self.tr("Fit"))
        self.fit_btn.setToolTip(
            self.tr("Zoom out to fit the panel (double-clicking the image does the same)")
        )
        self.actual_btn = QToolButton(header)
        self.actual_btn.setObjectName("WorkbenchPreviewToolBtn")
        self.actual_btn.setText("1:1")
        self.actual_btn.setToolTip(
            self.tr("Back to 100% scale (the review default)")
        )
        header_lay.addWidget(self._title, 1)
        header_lay.addWidget(self._zoom_label)
        header_lay.addWidget(self.fit_btn)
        header_lay.addWidget(self.actual_btn)
        layout.addWidget(header)
        self._header = header
        # 标题条里"除标题外"的固定控件（算浮层宽度地板时用，见 _header_rest_width）
        self._header_fixed = (self._zoom_label, self.fit_btn, self.actual_btn)

        self.canvas = _PreviewCanvas(self)
        layout.addWidget(self.canvas, 1)

        # ── 隐形拉伸手柄（左右下三边 + 四角；顶边让给标题条拖动）──
        self._handles = {}
        for zone in ("left", "right", "bottom", "top-left", "top-right",
                     "bottom-left", "bottom-right"):
            handle = _ResizeHandle(self, zone)
            handle.installEventFilter(self)
            self._handles[zone] = handle
        self._layout_handles()

        header.installEventFilter(self)
        self.fit_btn.clicked.connect(self.canvas.fit)
        self.actual_btn.clicked.connect(self.canvas.actual_size)
        self.canvas.scale_changed.connect(self._on_scale_changed)
        if host is not None:
            host.installEventFilter(self)
        self.hide()

    # ── 对外 ────────────────────────────────────────────────────

    def show_content(self, pixmap, caption: str = ""):
        """展示一张图（``pixmap=None`` 时只显示说明文字）；浮层自动弹出。

        每次换图都回到 100%（D23：审批图默认原比例，缩放由用户显式操作）。
        **不抢键盘焦点**：焦点留在候选列表上，↑/↓ 才能继续翻行——这也是
        Esc 走应用级过滤器的原因（``_on_global_event``）。
        """
        self._title.setText(caption)
        self.canvas.set_content(pixmap, caption)
        self._ensure_placed()
        self._fit_to_content()
        self.show()
        self.raise_()

    def close_panel(self):
        self.hide()
        self.closed.emit()

    def is_open(self) -> bool:
        return self.isVisible()

    # ── 内部 ────────────────────────────────────────────────────

    def _on_scale_changed(self, scale: float):
        self._zoom_label.setText(str(int(round(scale * 100))) + "%")

    def _fit_to_content(self):
        """把浮层收到"刚好装下这张图"（上限 ``MAX_SIZE``）。

        审批图的尺寸跨度很大（单气泡 134×43、跨栏组 500×900），固定尺寸要么
        让小图泡在空底里、要么平白盖住画布。用户手动调过就不再自动改。
        """
        if not self._auto_size:
            return
        host = self.parentWidget()
        content = self.canvas.pixmap_size()
        # 地板：标题条装得下"读数 + 两个按钮 + 整条标题"。标题按自己的文字
        # 宽度算（短标题整条可见），页面名过长才由 _ElidedTitle 截断。
        floor_w = max(
            self.MIN_SIZE.width(),
            self._header_rest_width()
            + self._title.natural_width(self.TITLE_MAX_WIDTH)
            + self.TITLE_SLACK
            + 2,
        )
        floor_h = self.MIN_SIZE.height()
        if content.isEmpty():
            width, height = floor_w, floor_h
        else:
            width = content.width() + 2 * self.CONTENT_MARGIN + 2
            height = (
                content.height() + self.HEADER_HEIGHT + 2 * self.CONTENT_MARGIN + 2
            )
        width = max(floor_w, min(width, self.MAX_SIZE.width()))
        height = max(floor_h, min(height, self.MAX_SIZE.height()))
        if host is not None:
            width = min(width, max(floor_w, host.width() - self.pos().x()))
            height = min(height, max(floor_h, host.height() - self.pos().y()))
        self.setMinimumWidth(floor_w)  # 手动拉伸也不许把标题条挤坏
        self.resize(width, height)

    def _header_rest_width(self) -> int:
        """标题条里除标题外的宽度（缩放读数 + 两个按钮 + 边距与间距）。

        **不能拿 ``header.sizeHint()`` 反推**：标题的 sizeHint 随省略号截断
        变化，反推出来的"其余宽度"跟着变，会变成正反馈。
        """
        layout = self._header.layout()
        margins = layout.contentsMargins()
        widgets = self._header_fixed
        return (
            sum(widget.sizeHint().width() for widget in widgets)
            + layout.spacing() * (len(widgets) + 1)
            + margins.left()
            + margins.right()
        )

    def _ensure_placed(self):
        host = self.parentWidget()
        if host is None:
            return
        if not self._placed:
            self._placed = True
            margin = 2 * self.ANCHOR_MARGIN
            self.resize(
                max(self.minimumWidth(), min(self.MAX_SIZE.width(), host.width() - margin)),
                max(self.minimumHeight(), min(self.MAX_SIZE.height(), host.height() - margin)),
            )
            self.move(self.ANCHOR_MARGIN, self.ANCHOR_MARGIN)
        # 宿主变小（或上次拖到边缘外）时收回可视区
        self.move(self._clamp_pos(self.pos()))
        self._clamp_size_to_host()

    def _clamp_pos(self, pos: QPoint) -> QPoint:
        host = self.parentWidget()
        if host is None:
            return pos
        return QPoint(
            max(0, min(pos.x(), host.width() - self.minimumWidth())),
            max(0, min(pos.y(), host.height() - self.HEADER_HEIGHT)),
        )

    def _clamp_size_to_host(self):
        host = self.parentWidget()
        if host is None:
            return
        width = max(self.minimumWidth(), min(self.width(), host.width() - self.pos().x()))
        height = max(self.minimumHeight(), min(self.height(), host.height() - self.pos().y()))
        if QSize(width, height) != self.size():
            self.resize(width, height)

    def _layout_handles(self):
        """手柄贴边铺开：边缘 5px 带、四角 14px 方块（顶边让给标题条）。"""
        width, height = self.width(), self.height()
        edge, corner = self.EDGE_MARGIN, self.CORNER_SIZE
        top = self.HEADER_HEIGHT
        zones = {
            "left": QRect(0, top, edge, max(0, height - top - corner)),
            "right": QRect(width - edge, top, edge, max(0, height - top - corner)),
            "bottom": QRect(corner, height - edge, max(0, width - 2 * corner), edge),
            "top-left": QRect(0, top, corner, corner),
            "top-right": QRect(width - corner, top, corner, corner),
            "bottom-left": QRect(0, height - corner, corner, corner),
            "bottom-right": QRect(width - corner, height - corner, corner, corner),
        }
        for zone, rect in zones.items():
            handle = self._handles[zone]
            handle.setGeometry(rect)
            handle.raise_()
        # 标题条压回去：它才是顶边的拖拽面
        self._header.raise_()
        self.canvas.lower()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_handles()

    def showEvent(self, event):
        super().showEvent(event)
        self._drag_origin = self.pos()
        self._layout_handles()
        self._install_global_filter(True)

    def hideEvent(self, event):
        super().hideEvent(event)
        self._install_global_filter(False)

    # ── 临时浮层的关闭路径（点画布 / Esc）──────────────────────

    def _install_global_filter(self, on: bool):
        """只在浮层可见期间挂应用级过滤器（关掉即摘，不给全局留钩子）。"""
        app = QApplication.instance()
        if app is None or on == self._filter_installed:
            return
        if on:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)
        self._filter_installed = on

    def _on_global_event(self, event) -> bool:
        """点宿主（画布区）或按 Esc ＝ 关闭；**一律不吞事件**。

        返回 ``False`` 是刻意的：画布／其他控件自己的 Esc 语义（例如取消
        当前操作）不该因为浮层开着就失效。浮层自己身上的点击不算"点画布"。
        """
        kind = event.type()
        if kind == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.close_panel()
            return False
        if kind != QEvent.Type.MouseButtonPress or not isinstance(event, QMouseEvent):
            return False
        host = self.parentWidget()
        if host is None:
            return False
        global_pos = event.globalPosition().toPoint()
        if self.rect().contains(self.mapFromGlobal(global_pos)):
            return False  # 点在浮层自己身上：不关
        if host.rect().contains(host.mapFromGlobal(global_pos)):
            self.close_panel()
        return False

    def eventFilter(self, obj, event):
        if obj is self._header:
            if self._on_header_event(event):
                return True
        elif isinstance(obj, _ResizeHandle):
            if self._on_handle_event(obj.zone, event):
                return True
        elif obj is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.move(self._clamp_pos(self.pos()))
            self._clamp_size_to_host()
        elif self._filter_installed:
            # 应用级过滤器（只在浮层可见期间挂着）。**`obj` 是事件的目标对象，
            # 不是 app 自己**——Qt 走的是 sendThroughApplicationEventFilters，
            # 它把 receiver 传进来，所以这里不能按"obj 是不是 app"来判断。
            return self._on_global_event(event)
        return super().eventFilter(obj, event)

    def _on_header_event(self, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._press_pos = event.globalPosition().toPoint()
                self._drag_origin = self.pos()
                return True
        elif kind == QEvent.Type.MouseMove:
            if self._press_pos is not None:
                delta = event.globalPosition().toPoint() - self._press_pos
                if delta:  # 真的动了才交出尺寸控制权
                    self._auto_size = False
                self.move(self._clamp_pos(self._drag_origin + delta))
                return True
        elif kind == QEvent.Type.MouseButtonRelease:
            if self._press_pos is not None:
                self._press_pos = None
                return True
        return False

    def _on_handle_event(self, zone: str, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._press_pos = event.globalPosition().toPoint()
                self._press_geom = self.geometry()
                return True
        elif kind == QEvent.Type.MouseMove:
            if self._press_pos is not None and self._press_geom is not None:
                delta = event.globalPosition().toPoint() - self._press_pos
                if delta:
                    self._auto_size = False
                self.setGeometry(self._resized_geom(zone, delta))
                return True
        elif kind == QEvent.Type.MouseButtonRelease:
            if self._press_pos is not None:
                self._press_pos = None
                self._press_geom = None
                return True
        return False

    def _resized_geom(self, zone: str, delta: QPoint) -> QRect:
        geom = QRect(self._press_geom)
        host = self.parentWidget()
        if "left" in zone:
            geom.setLeft(geom.left() + delta.x())
        if "right" in zone:
            geom.setRight(geom.right() + delta.x())
        if "top" in zone:
            geom.setTop(geom.top() + delta.y())
        if "bottom" in zone:
            geom.setBottom(geom.bottom() + delta.y())
        # 地板尺寸：拖过头时钉住被拖的那条边
        if geom.width() < self.minimumWidth():
            if "left" in zone:
                geom.setLeft(geom.right() - self.minimumWidth() + 1)
            else:
                geom.setRight(geom.left() + self.minimumWidth() - 1)
        if geom.height() < self.minimumHeight():
            if "top" in zone:
                geom.setTop(geom.bottom() - self.minimumHeight() + 1)
            else:
                geom.setBottom(geom.top() + self.minimumHeight() - 1)
        if geom.left() < 0:
            geom.setLeft(0)
        if geom.top() < 0:
            geom.setTop(0)
        if host is not None:
            if geom.right() > host.width() - 1:
                geom.setRight(host.width() - 1)
            if geom.bottom() > host.height() - 1:
                geom.setBottom(host.height() - 1)
        return geom
