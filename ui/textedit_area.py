from typing import List

from qtpy.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
    Signal,
)
from qtpy.QtGui import (
    QColor,
    QFocusEvent,
    QInputMethodEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QTextCursor,
)
from qtpy.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from utils.config import pcfg

from ui.misc import get_theme_color

from .custom_widget import ScrollBar, Widget
from .textitem import TextBlock

# Styling moved to config/stylesheet.css with dynamic property selectors.
# TransPairWidget card states (checked, hover) are now controlled
# via setProperty() + unpolish/polish, not setStyleSheet().

# Width of the drag handle zone to the right of accent_bar (always shown)
DRAG_AREA_WIDTH = 22


class SourceTextEdit(QTextEdit):
    hover_enter = Signal(int)
    hover_leave = Signal(int)
    focus_in = Signal(int)
    propagate_user_edited = Signal()
    ensure_scene_visible = Signal()
    redo_signal = Signal()
    undo_signal = Signal()
    push_undo_stack = Signal()
    text_changed = Signal()
    show_select_menu = Signal(QPoint, str)
    focus_out = Signal(int)

    def __init__(self, idx, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.idx = idx
        self.pre_editing = False
        self.document().contentsChanged.connect(self.on_content_changed)
        self.document().documentLayout().documentSizeChanged.connect(self.adjustSize)
        self.document().contentsChange.connect(self.on_content_changing)
        self.setAcceptRichText(False)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        # 撤销体系 3b：文档私有栈全面禁用，撤销重做唯一入口是
        # canvas.text_undo_stack 的快照命令（ui/canvas.py::undo_textedit）。
        self.document().setUndoRedoEnabled(False)
        self.in_redo_undo = False
        self.text_content_changed = False
        self.highlighting = False
        # 最近一次内容变更的坐标参数（contentsChange 原样记录），编辑会话
        # 管理器据此判断键入相邻性（burst 边界），见
        # ui/canvas.py::Canvas.note_typing_edit
        self.change_from = 0
        self.change_removed = 0
        self.change_added = 0

        self.selected_text = ""
        self.cursorPositionChanged.connect(self.on_cursorpos_changed)

        self.cursor_coord = None
        self.block_all_input = False
        self.in_acts = False

        # NoFrame + transparent viewport → CSS border-radius shows through.
        self.setFrameStyle(QFrame.NoFrame)
        self.viewport().setAutoFillBackground(False)

        # Small internal padding so the edit cursor doesn't bump the edge
        # and cause text to shift on focus (margin=0 → cursor flush with edge
        # forces a re-layout on every click).
        self.document().setDocumentMargin(2)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.min_height = 45
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)

    def contextMenuEvent(self, event):
        pass

    def on_cursorpos_changed(self) -> None:
        cursor = self.textCursor()
        if cursor.hasSelection():
            self.selected_text = cursor.selectedText()
            crect = self.cursorRect()
            if cursor.selectionStart() == cursor.position():
                self.cursor_coord = crect.bottomLeft()
            else:
                self.cursor_coord = crect.bottomRight()
        else:
            if self.cursor_coord is not None:
                self.show_select_menu.emit(QPoint(), "")
            self.cursor_coord = None

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        super().mouseReleaseEvent(e)
        if e.button() == Qt.MouseButton.LeftButton:
            if self.hasFocus():
                if self.cursor_coord is not None:
                    pos = self.mapToGlobal(self.cursor_coord)
                    sel_text = self.selected_text
                    self.show_select_menu.emit(pos, sel_text)

    def block_all_signals(self, block: bool):
        self.blockSignals(block)
        self.document().blockSignals(block)

    def on_content_changing(self, from_: int, removed: int, added: int):
        if not self.pre_editing:
            self.text_content_changed = True
            self.change_from = from_
            self.change_removed = removed
            self.change_added = added

    def adjustSize(self):
        h = self.document().documentLayout().documentSize().toSize().height()
        self.setFixedHeight(max(h, self.min_height))

    def on_content_changed(self):
        if self.text_content_changed:
            self.text_content_changed = False
            if not self.highlighting:
                self.text_changed.emit()

        if (
            not self.pre_editing
            and not self.highlighting
            and not self.in_acts
        ):
            self.handle_content_change()

    def handle_content_change(self):
        if not self.in_redo_undo:
            # 位置式差值重放已废弃：对账基于两份文档的当前全文现场计算，
            # 这里只负责通知下游（见 sync_text_by_diff）。撤销落账由
            # canvas 编辑会话按内容变更驱动（原文编辑器无镜像，靠本
            # push 信号登记）。
            self.propagate_user_edited.emit()
            self.push_undo_stack.emit()

    def setHoverEffect(self, hover: bool):
        """Visual hover feedback handled via CSS :hover/:focus in stylesheet.css.
        This method is kept as a no-op for signal emission in enter/leave/focus events."""
        pass

    def enterEvent(self, event: QEvent) -> None:
        self.setHoverEffect(True)
        self.hover_enter.emit(self.idx)
        return super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.setHoverEffect(False)
        self.hover_leave.emit(self.idx)
        return super().leaveEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:
        self.setHoverEffect(True)
        self.focus_in.emit(self.idx)
        self.pre_editing = False
        return super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        self.setHoverEffect(False)
        self.focus_out.emit(self.idx)
        return super().focusOutEvent(event)

    def inputMethodEvent(self, e: QInputMethodEvent) -> None:
        # pre_editing 只决定组合期是否跳过对账（组合中间态不写回画布）；
        # 提交产生的内容变更会走常规 on_content_changed 路径。
        if e.preeditString() == "":
            self.pre_editing = False
        else:
            self.pre_editing = True
        super().inputMethodEvent(e)

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if self.block_all_input:
            e.setAccepted(True)
            return

        if e.modifiers() == Qt.KeyboardModifier.ControlModifier:
            if e.key() == Qt.Key.Key_Z:
                e.accept()
                self.undo_signal.emit()
                return
            elif e.key() == Qt.Key.Key_Y:
                e.accept()
                self.redo_signal.emit()
                return
            elif e.key() == Qt.Key.Key_V:
                return super().keyPressEvent(e)
        elif (
            e.modifiers()
            == Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        ):
            if e.key() == Qt.Key.Key_Z:
                e.accept()
                self.redo_signal.emit()
                return
        elif e.key() == Qt.Key.Key_Return:
            e.accept()
            self.textCursor().insertText("\n")
            return
        return super().keyPressEvent(e)

    def setPlainTextAndKeepUndoStack(self, text: str):
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)

    def insert_external_text(self, text: str):
        """Insert text from outside (e.g. the soft keyboard panel).

        The document change routes through the regular
        ``contentsChanged → on_content_changed → handle_content_change``
        chain exactly like typing — do NOT call ``handle_content_change``
        here as well, that double-fires the undo/propagate signals (each
        insert registered twice and split the source typing session).
        """
        cursor = self.textCursor()
        cursor.insertText(text)
        self.setTextCursor(cursor)


class TransTextEdit(SourceTextEdit):
    pass


class TransPairWidget(Widget):
    check_state_changed = Signal(object, bool, bool)

    def __init__(
        self,
        textblock: TextBlock = None,
        idx: int = None,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.e_source = SourceTextEdit(idx, self)
        self.e_trans = TransTextEdit(idx, self)
        self.textblock = textblock
        self.idx = idx

        # ── Index badge ─────────────────────────────────────────
        # Number badge inside the left drag zone (always shown).
        self.badge = QLabel()
        self.badge.setObjectName("TextBlockIndexBadge")
        self.badge.setText(str(idx + 1))
        self.badge.setContentsMargins(2, 0, 2, 0)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.adjustSize()
        self.badge.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )

        self.checked = False
        self._is_hovered = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        vlayout = QVBoxLayout()
        vlayout.setAlignment(Qt.AlignTop)
        vlayout.addWidget(self.e_source)
        vlayout.addWidget(self.e_trans)
        spacing = 2
        vlayout.setSpacing(spacing)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContentsMargins(0, 0, 0, 0)
        # right=3 to match left side accent_bar width after spacing is 0
        vlayout.setContentsMargins(spacing, spacing, 3, spacing)

        # Left accent bar for checked-state indicator
        self.accent_bar = QFrame(self)
        self.accent_bar.setObjectName("accentBar")
        self.accent_bar.setFixedWidth(3)
        # Start hidden — CSS rule TransPairWidget #accentBar would otherwise
        # render it at full opacity before the first animation runs.
        self.accent_bar.setStyleSheet("background: transparent;")
        # Avoid QGraphicsOpacityEffect: its offscreen cache breaks rendering
        # inside QScrollArea (bar "sticks" in place during scroll).  Instead
        # animate background alpha via timer + setStyleSheet.
        self._accent_alpha = 0.0  # 0.0=hidden, 1.0=full
        self._accent_timer = None
        self.accent_bar.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )

        # Drag handle zone — sits between accent_bar and text content.
        # Provides space for drag initiation and contains the number badge
        # (vertically centered via layout); always shown.
        self.drag_area = QFrame(self)
        self.drag_area.setObjectName("dragArea")
        self.drag_area.setFixedWidth(DRAG_AREA_WIDTH)
        self.drag_area.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        drag_layout = QVBoxLayout(self.drag_area)
        drag_layout.setContentsMargins(0, 0, 0, 0)
        drag_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drag_layout.addWidget(self.badge)

        hlayout = QHBoxLayout(self)
        hlayout.addWidget(self.accent_bar)
        hlayout.addWidget(self.drag_area)
        hlayout.addLayout(vlayout)
        hlayout.setContentsMargins(0, 0, 0, 0)
        hlayout.setSpacing(0)  # all spacing managed by vlayout margins

    def _set_checked_state(self, checked: bool):
        """
        this wont emit state_change signal and take care of the style
        """
        if self.checked != checked:
            self.checked = checked
            # Use dynamic property so stylesheet rules can style the card
            self.setProperty("checked", checked)
            self.style().unpolish(self)
            self.style().polish(self)

            # Animate accent bar opacity (no QGraphicsOpacityEffect — it
            # breaks inside QScrollArea).  Use timer-based color alpha fade.
            self._animate_accent_alpha(1.0 if checked else 0.0)

    def _animate_accent_alpha(self, target: float):
        """Fade accent bar background alpha toward *target* (0.0–1.0)."""
        if self._accent_timer is not None:
            self._accent_timer.stop()
            self._accent_timer = None

        duration = 150  # ms
        steps = max(2, duration // 16)  # ~60 fps
        step_ms = duration // steps
        start = self._accent_alpha
        delta = target - start

        self._accent_timer = QTimer(self)
        self._accent_timer.timeout.connect(self._accent_tick)
        self._accent_tick_data = (start, delta, target, steps, step_ms)
        self._accent_step = 0
        self._accent_timer.start(step_ms)

    def _accent_tick(self):
        """Tick handler for accent bar fade animation."""
        start, delta, target, steps, step_ms = self._accent_tick_data
        self._accent_step += 1
        progress = min(self._accent_step / steps, 1.0)
        self._accent_alpha = start + delta * progress
        alpha_int = max(0, min(255, int(self._accent_alpha * 255)))
        if alpha_int <= 0:
            self.accent_bar.setStyleSheet("background: transparent;")
        else:
            c = get_theme_color(alpha=alpha_int)
            self.accent_bar.setStyleSheet(
                f"background-color: rgba({c.red()},{c.green()},{c.blue()},{alpha_int});"
                "border-radius: 1px;"
            )
        if progress >= 1.0:
            self._accent_timer.stop()
            self._accent_timer = None
            self._accent_tick_data = None

    def enterEvent(self, event):
        self._is_hovered = True
        return super().enterEvent(event)

    def leaveEvent(self, event):
        self._is_hovered = False
        return super().leaveEvent(event)

    def update_checkstate_by_mousevent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            modifiers = e.modifiers()
            if (
                modifiers & Qt.KeyboardModifier.ShiftModifier
                and modifiers & Qt.KeyboardModifier.ControlModifier
            ):
                shift_pressed = ctrl_pressed = True
            else:
                shift_pressed = modifiers == Qt.KeyboardModifier.ShiftModifier
                ctrl_pressed = modifiers == Qt.KeyboardModifier.ControlModifier
            self.check_state_changed.emit(self, shift_pressed, ctrl_pressed)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if not self.checked:
            self.update_checkstate_by_mousevent(e)
        return super().mousePressEvent(e)

    def updateIndex(self, idx: int):
        if self.idx != idx:
            self.idx = idx
            text = str(idx + 1)
            self.badge.setText(text)
            self.badge.adjustSize()
            self.e_source.idx = idx
            self.e_trans.idx = idx


class _DragGapFrame(QFrame):
    """落点槽位指示框：半透明强调色底 + 加粗虚线描边。

    QSS 的 ``border-style: dashed`` 虚线段细且段长不可调，实测难以辨认，
    改用 QPainter 自绘：笔宽 / 虚线段长 / 圆角全部可控，强调色走主题
    变量（ui/misc.py::get_theme_color），随主题即时取色。
    """

    PEN_WIDTH = 3
    # 单位=笔宽：3px 笔宽下虚线段 9px、间隔 5.4px
    DASH_PATTERN = (3.0, 1.8)
    RADIUS = 6
    FILL_ALPHA = 46

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        accent = get_theme_color(key="@accentPrimary")
        inset = self.PEN_WIDTH // 2 + 1
        rect = self.rect().adjusted(inset, inset, -inset, -inset)
        fill = QColor(accent)
        fill.setAlpha(self.FILL_ALPHA)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(rect, self.RADIUS, self.RADIUS)
        pen = QPen(accent, self.PEN_WIDTH)
        pen.setDashPattern(list(self.DASH_PATTERN))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, self.RADIUS, self.RADIUS)


class TextEditListScrollArea(QScrollArea):
    textblock_list: List[TextBlock] = []
    pairwidget_list: List[TransPairWidget] = []
    remove_textblock = Signal()
    selection_changed = (
        Signal()
    )  # this signal could only emit in on_widget_checkstate_changed, i.e. via user op
    rearrange_blks = Signal(object)
    textpanel_contextmenu_requested = Signal(QPoint, bool)
    focus_out = Signal()

    # 类级默认：QScrollArea.setWidget 期间 Qt 会回调 eventFilter（早于
    # __init__ 里的实例属性赋值），回调内读 _drag_active 不能踩空
    _drag_active = False

    # 堆叠折叠：多选拖拽时第 i 张卡相对堆顶下沉的像素（各卡头顶条连同
    # 徽标依次可辨）；gap 槽高按折叠后的堆高取值
    PILE_PEEK = 18
    # 拖拽期间盖在非拖拽内容上的变暗遮罩不透明度（0-255，约 15%）
    DIM_ALPHA = 38

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # ── 行拖拽状态（自定义抓取式，见 begin_rows_drag）──
        # 必须先于 setWidget 初始化：eventFilter 在构造期即被回调
        self._drag_pws: List[TransPairWidget] = []   # 被拖组（按原 idx 排序）
        self._rest: List[TransPairWidget] = []       # 未拖行（原顺序）
        self._rest_y = {}                            # 让位排布的目标 y
        self._gap_slot = 0                           # 落点槽位（插在第 N 个 rest 行前）
        self._spacing = 0
        self._base_y = self._base_x = self._card_w = 0
        self._gap_h = 0
        self._drag_cursor_vp_y = 0.0
        self._pile_offsets: List[int] = []           # 被拖组折叠偏移（堆顶=0）
        self._drag_dim: QWidget = None
        self._gap_frame: QFrame = None
        self._pos_anims = {}
        self._auto_timer: QTimer = None
        self._auto_speed = 0

        self.scrollContent = Widget(parent=self)
        self.setWidget(self.scrollContent)

        # Custom scrollbar — upstream Fluent-style, auto-fade on idle
        ScrollBar(Qt.Orientation.Vertical, self, fadeout=True)

        vlayout = QVBoxLayout(self.scrollContent)
        vlayout.setContentsMargins(0, 0, 0, 0)
        vlayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        vlayout.setSpacing(6)
        vlayout.addStretch(1)
        self.setWidgetResizable(True)
        self.vlayout = vlayout
        self.checked_list: List[TransPairWidget] = []
        self.sel_anchor_widget: TransPairWidget = None
        self.dragStartPosition = None

        self.source_visible = True
        self.trans_visible = True

        self.setSizePolicy(
            self.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Expanding
        )
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if self._drag_active:
            if e.button() == Qt.MouseButton.LeftButton:
                self._finish_drag()
            return  # 拖拽期间吞掉其它按键释放（右键菜单等）
        if e.button() == Qt.MouseButton.RightButton:
            pos = self.mapToGlobal(e.position()).toPoint()
            self.textpanel_contextmenu_requested.emit(pos, True)
        self.dragStartPosition = None
        super().mouseReleaseEvent(e)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if self._drag_active:
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self.dragStartPosition = e.pos()
        return super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag_active:
            self._drag_cursor_vp_y = e.position().y()
            self._update_drag_frame()
            return
        if (
            self.sel_anchor_widget is not None
            and self.dragStartPosition is not None
        ):
            if (
                e.pos() - self.dragStartPosition
            ).manhattanLength() < QApplication.startDragDistance():
                return
            self.dragStartPosition = None
            self.begin_rows_drag(e.position().y())

        return super().mouseMoveEvent(e)

    def wheelEvent(self, e) -> None:
        super().wheelEvent(e)
        if self._drag_active:
            # 滚轮照常滚动，但内容坐标变了，同步拖拽组与让位排布
            self._update_drag_frame()

    # ── 行拖拽：抓取式实时让位 ─────────────────────────────────
    # 与原生 QDrag 的差异：拖拽期间鼠标抓取在视口上（hover 不会串到
    # 输入框），被拖组保持真身渲染、堆叠折叠跟随光标，非拖拽内容盖
    # 变暗遮罩，落点处留自绘虚线指示框，其余行实时让位动画；块列表
    # 顺序只在松手时经 rearrange_blks 落账，拖拽全程只动 UI 不动数据。

    def begin_rows_drag(self, cursor_vp_y: float) -> None:
        """启动行拖拽。*cursor_vp_y* 为触发时视口坐标 y（拖拽组锚定用）。"""
        if self._drag_active:
            return
        n = len(self.pairwidget_list)
        drags = sorted(self.checked_list, key=lambda w: w.idx)
        if n < 2 or not drags or len(drags) == n:
            return
        self._drag_active = True
        self._drag_pws = drags
        self._rest = [w for w in self.pairwidget_list if w not in drags]
        self._gap_slot = drags[0].idx
        self._spacing = self.vlayout.spacing()
        self._base_y = min(w.y() for w in self.pairwidget_list)
        self._base_x = self._rest[0].x()
        self._card_w = self._rest[0].width()
        # 多选折叠成堆：第 i 张卡相对堆顶下沉 i*PILE_PEEK，gap 槽高取
        # 折叠后的实际堆高（松手落账的空位随之缩减）
        self._pile_offsets = [i * self.PILE_PEEK for i in range(len(drags))]
        self._gap_h = max(
            off + w.height() for off, w in zip(self._pile_offsets, drags)
        )
        # 聚拢锚点 = 触发时鼠标的内容坐标（堆顶对齐光标），各卡从
        # 原位聚拢动画飞向光标；此后堆顶以追随补间咬合光标
        pile_top = int(cursor_vp_y + self.verticalScrollBar().value())
        self._drag_cursor_vp_y = cursor_vp_y
        # 上一局的退应动画可能仍在飞（快速连拖），先停干净再接管
        for anim in self._pos_anims.values():
            try:
                anim.stop()
            except RuntimeError:
                pass
        self._pos_anims = {}

        QApplication.instance().installEventFilter(self)
        # 失活取消主路径走信号：Windows 上 app 级过滤器收
        # ApplicationDeactivate 事件不可靠（2026-08-18 教训，同
        # ui/mainwindow.py 饼菜单的规避方案），applicationStateChanged
        # 信号才是可靠送达的。
        QApplication.instance().applicationStateChanged.connect(
            self._on_app_state_changed
        )
        self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        self.viewport().grabMouse()

        # 布局接管：行全部移出布局改为手动定位。LayoutRequest 异步激活时
        # 布局里只剩 stretch，不会动手动定位的行；最小高度兜底防
        # widgetResizable 在布局塌缩后把 scrollContent 缩掉（滚动条失效）
        self.scrollContent.setMinimumHeight(self.scrollContent.height())
        for w in self.pairwidget_list:
            self.vlayout.removeWidget(w)

        # 变暗遮罩：盖住非拖拽内容（WA_StyledBackground 让纯 QWidget
        # 生效 QSS 背景色），拖拽组与指示框浮在其上保持原生全分辨率渲染
        self._drag_dim = QWidget(self.scrollContent)
        self._drag_dim.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._drag_dim.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._drag_dim.setStyleSheet(
            f"background-color: rgba(0, 0, 0, {self.DIM_ALPHA});"
        )
        self._drag_dim.setGeometry(
            0, 0, self.scrollContent.width(), self.scrollContent.height()
        )
        self._drag_dim.show()

        # 落点指示框（位置随 _apply_arrangement 刷新）
        self._gap_frame = _DragGapFrame(self.scrollContent)
        self._gap_frame.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )

        # 被拖组保持真身可见：聚拢动画飞向光标锚点；z 序按原顺序
        # 后位卡在上，各卡头顶条（含编号徽标）依次可见
        for w, off in zip(drags, self._pile_offsets):
            self._move_card(w, pile_top + off, animate=True)
        self._apply_arrangement(animate=True)
        self._gap_frame.show()
        self._drag_dim.raise_()
        self._gap_frame.raise_()
        for w in drags:
            w.raise_()

    def _arrange_targets(self):
        """让位排布：rest 行按序堆叠，拖拽组槽位（gap）插在第
        ``_gap_slot`` 个 rest 行之前。返回 (各行目标 y, gap 顶 y)。"""
        ys = {}
        y = self._base_y
        gap_top = self._base_y
        placed = False
        for i, w in enumerate(self._rest):
            if i == self._gap_slot:
                gap_top = y
                y += self._gap_h + self._spacing
                placed = True
            ys[w] = y
            y += w.height() + self._spacing
        if not placed:
            gap_top = y  # gap 在末尾：最后一行底下
        return ys, gap_top

    def _apply_arrangement(self, animate: bool):
        ys, gap_top = self._arrange_targets()
        self._rest_y = ys
        for w, ty in ys.items():
            self._move_card(w, ty, animate)
        if self._gap_frame is not None:
            self._gap_frame.setGeometry(
                self._base_x, gap_top, self._card_w, self._gap_h
            )

    def _move_card(self, pw: TransPairWidget, target_y: int, animate: bool):
        if pw.y() == target_y:
            return
        old = self._pos_anims.pop(pw, None)
        if old is not None:
            try:
                old.stop()
            except RuntimeError:
                pass
        if not animate or pcfg.animation_fps < 0:
            pw.move(pw.x(), target_y)
            return
        anim = QPropertyAnimation(pw, b"pos", self)
        anim.setDuration(140)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(pw.pos())
        anim.setEndValue(QPoint(pw.x(), target_y))
        self._pos_anims[pw] = anim

        def _anim_cleanup():
            # 只在仍登记的是本动画时移除（stop-重启场景不误删新动画）
            if self._pos_anims.get(pw) is anim:
                del self._pos_anims[pw]

        anim.finished.connect(_anim_cleanup)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _update_gap(self, y_cursor: int):
        """让位判定（邻行中点穿越）：光标越过 gap 上邻行的中点则 gap
        上移一格，越过下邻行中点则下移。以让位排布的目标坐标（而非
        动画当前位置）计算，避免与进行中的位移动画形成反馈；while 循环
        吸收一次事件跨多行的快拖。"""
        while True:
            if self._gap_slot > 0:
                above = self._rest[self._gap_slot - 1]
                if y_cursor < self._rest_y[above] + above.height() / 2:
                    self._gap_slot -= 1
                    self._apply_arrangement(animate=True)
                    continue
            if self._gap_slot < len(self._rest):
                below = self._rest[self._gap_slot]
                if y_cursor > self._rest_y[below] + below.height() / 2:
                    self._gap_slot += 1
                    self._apply_arrangement(animate=True)
                    continue
            break

    def _update_drag_frame(self):
        sb = self.verticalScrollBar()
        y_content = int(self._drag_cursor_vp_y + sb.value())
        for w, off in zip(self._drag_pws, self._pile_offsets):
            # 追随式跟随：拖拽组永远朝光标做补间（140ms OutCubic），
            # 鼠标每动一次重定目标——聚拢动画天然可见，任何时刻都不
            # 瞬移；目标未变则不重启（防高频鼠标事件反复创建动画对象）。
            # 让位判定用的是光标坐标（_update_gap），不受视觉滞后影响。
            ty = y_content + off
            old = self._pos_anims.get(w)
            if old is not None:
                try:
                    if old.endValue().y() == ty:
                        continue
                except RuntimeError:
                    pass
            self._move_card(w, ty, animate=True)
        self._update_gap(y_content)
        # 视口边缘自动滚动（靠近上下缘 30px 内，越近越快）
        vp_h = self.viewport().height()
        edge = 30
        speed = 0
        if self._drag_cursor_vp_y < edge:
            speed = -max(3, int((edge - self._drag_cursor_vp_y) * 0.25))
        elif self._drag_cursor_vp_y > vp_h - edge:
            speed = max(3, int((self._drag_cursor_vp_y - (vp_h - edge)) * 0.25))
        if speed:
            self._auto_speed = speed
            if self._auto_timer is None:
                t = QTimer(self)
                t.timeout.connect(self._auto_scroll_tick)
                self._auto_timer = t
                t.start(16)
        elif self._auto_timer is not None:
            self._auto_timer.stop()
            self._auto_timer.deleteLater()
            self._auto_timer = None

    def _auto_scroll_tick(self):
        sb = self.verticalScrollBar()
        sb.setValue(sb.value() + self._auto_speed)
        self._update_drag_frame()

    def _restore_layout(self, order: List[TransPairWidget]):
        for i, w in enumerate(order):
            self.vlayout.insertWidget(i, w)
        self.scrollContent.setMinimumHeight(0)

    def _teardown_drag(self):
        """拖拽收尾（落账/取消共用）：释放抓取、摘过滤器、清浮层与动画。"""
        self._drag_active = False
        QApplication.instance().removeEventFilter(self)
        try:
            QApplication.instance().applicationStateChanged.disconnect(
                self._on_app_state_changed
            )
        except (TypeError, RuntimeError):
            pass
        try:
            self.viewport().releaseMouse()
        except RuntimeError:
            pass
        self.viewport().unsetCursor()
        if self._auto_timer is not None:
            self._auto_timer.stop()
            self._auto_timer.deleteLater()
            self._auto_timer = None
        for w in (self._drag_dim, self._gap_frame):
            if w is not None:
                w.hide()
                w.deleteLater()
        self._drag_dim = None
        self._gap_frame = None
        for anim in list(self._pos_anims.values()):
            try:
                anim.stop()
            except RuntimeError:
                pass
        self._pos_anims = {}

    def _finish_drag(self):
        """松手落账：快照当前位置 → 布局按新序归还并同步激活到终态
        → 经 rearrange_blks 即时落账（几何所见即终态，消费端补间自动
        跳过，数据零延迟窗口）→ 把行搬回快照位置，整体退应飞向布局
        终态（被拖组从堆叠展开落槽、rest 行从让位位微调），动画风格
        与拖拽中的位移动画一致。"""
        if not self._drag_active:
            return
        self._teardown_drag()
        new_order = (
            self._rest[: self._gap_slot] + self._drag_pws + self._rest[self._gap_slot:]
        )
        start_ys = {w: w.y() for w in self.pairwidget_list}
        self._restore_layout(new_order)
        # 强制同步激活布局：几何即刻到位，落账对比所见即终态
        self.vlayout.activate()
        self._drag_pws = []
        self._rest = []
        self._emit_rearrange_from_perm([w.idx for w in new_order])
        self._settle_to_layout(start_ys)

    def _settle_to_layout(self, start_ys: dict) -> None:
        """退应动画：行从快照位置飞向（已同步激活的）布局终态；
        关动画（animation_fps < 0）时保持就地终态不补间。"""
        if pcfg.animation_fps < 0:
            return
        finals = {w: w.y() for w in self.pairwidget_list}
        for w, sy in start_ys.items():
            if sy == finals[w]:
                continue
            w.move(w.x(), sy)
            self._move_card(w, finals[w], animate=True)

    def _cancel_drag(self):
        if not self._drag_active:
            return
        self._teardown_drag()
        start_ys = {w: w.y() for w in self.pairwidget_list}
        self._restore_layout(self.pairwidget_list)  # 原顺序原位
        self.vlayout.activate()
        self._drag_pws = []
        self._rest = []
        self._settle_to_layout(start_ys)

    def clearDrag(self):
        """外部清拖请求（焦点切走等）：拖拽进行中则取消。"""
        if self._drag_active:
            self._cancel_drag()

    def _on_app_state_changed(self, state) -> None:
        """应用整体失活（截图浮层/切走窗口等）即取消拖拽，行原位还原。"""
        if self._drag_active and state != Qt.ApplicationState.ApplicationActive:
            self._cancel_drag()

    def eventFilter(self, obj, event) -> bool:
        if self._drag_active:
            t = event.type()
            if t == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self._cancel_drag()
                return True
            if t == QEvent.Type.ApplicationDeactivate:
                # 失焦兜底（主路径是 applicationStateChanged 信号）：截图
                # 浮层等外部窗口接管鼠标时拖拽组冻在原地却仍响应滚轮
                # 移位，回来随手一点就会把冻结位置误落账。对齐原生
                # QDrag 的失焦取消语义，直接取消还原。
                self._cancel_drag()
                return False
            if t in (QEvent.Type.HoverEnter, QEvent.Type.HoverMove):
                # 拖拽期间吞掉列表内部的 hover，防止输入框误亮
                # （鼠标抓取理论上已隔离，此处兜底）
                w = obj if isinstance(obj, QWidget) else None
                while w is not None and w is not self.scrollContent:
                    w = w.parentWidget()
                if w is self.scrollContent:
                    return True
        return super().eventFilter(obj, event)

    def _emit_rearrange_from_perm(self, result_list):
        """Compute (drags_ori, drags_tgt) from a permutation list (each entry = old idx
        at that position), emit rearrange_blks so on_rearrange_blks -> RearrangeBlksCommand
        runs through the same path as drag-drop. Items unchanged are filtered out, so
        unchanged blocks stay out of tgt_ids (updateTextBlkItemIdx won't touch them).
        """
        drags_ori, drags_tgt = [], []
        for ii, idx in enumerate(result_list):
            if ii != idx:
                drags_ori.append(idx)
                drags_tgt.append(ii)
        if drags_ori:
            self.rearrange_blks.emit((drags_ori, drags_tgt))

    def move_selected(self, mode: str, to_pos: int = None):
        """Reorder selected widgets as a group. Called by ReorderContent buttons / Go.

        mode: "up" / "down" / "top" / "bottom" / "to_pos"
        to_pos: 1-based, only for "to_pos"

        整组移动语义：选中块从列表取出后作为一个连续整体,插入到目标位置之前。
        记 result_list[i] = 移动后应在 result 第 i 位的旧 idx。建构方式
        ``others[:insert_at] + sel_idxs + others[insert_at:]``，其中
        ``insert_at`` 同时也是 group 在 result 中的起始 0-based 位置。
        """
        n = len(self.pairwidget_list)
        sel_idxs = sorted(pw.idx for pw in self.checked_list)
        num_sel = len(sel_idxs)
        if n < 2 or num_sel == 0 or num_sel == n:
            return

        sel_set = set(sel_idxs)
        first_sel, last_sel = sel_idxs[0], sel_idxs[-1]
        others = [i for i in range(n) if i not in sel_set]

        if mode == "top":
            if first_sel == 0:
                return
            insert_at = 0
        elif mode == "bottom":
            if last_sel == n - 1:
                return
            insert_at = len(others)
        elif mode == "up":
            if first_sel == 0:
                return
            insert_at = first_sel - 1
        elif mode == "down":
            if first_sel + num_sel >= n:
                return
            insert_at = first_sel + 1
        elif mode == "to_pos":
            # to_pos (1-based) = "插到第 to_pos 块之前". 第 to_pos 块 0-based idx = to_pos-1.
            target_idx = to_pos - 1
            if target_idx < 0 or target_idx >= n:
                return
            if target_idx in sel_set:
                return
            # group 起始 result 位置 = target_idx 之前的非选中数量 = others 中 target_idx 的下标
            insert_at = target_idx - sum(1 for s in sel_idxs if s < target_idx)
        else:
            return

        if insert_at < 0:
            insert_at = 0
        if insert_at > len(others):
            insert_at = len(others)

        result_list = others[:insert_at] + sel_idxs + others[insert_at:]
        if len(result_list) != n:
            return
        self._emit_rearrange_from_perm(result_list)

    def addPairWidget(self, pairwidget: TransPairWidget):
        self.vlayout.insertWidget(pairwidget.idx, pairwidget)
        pairwidget.check_state_changed.connect(self.on_widget_checkstate_changed)
        pairwidget.e_trans.setVisible(self.trans_visible)
        pairwidget.e_source.setVisible(self.source_visible)
        pairwidget.setVisible(True)

    def insertPairWidget(self, pairwidget: TransPairWidget, idx: int):
        self.vlayout.insertWidget(idx, pairwidget)
        pairwidget.e_trans.setVisible(self.trans_visible)
        pairwidget.e_source.setVisible(self.source_visible)
        pairwidget.setVisible(True)

    def on_widget_checkstate_changed(
        self, pwc: TransPairWidget, shift_pressed: bool, ctrl_pressed: bool
    ):
        if self._drag_active:
            return

        idx = pwc.idx
        if shift_pressed:
            checked = True
        else:
            checked = not pwc.checked
        pwc._set_checked_state(checked)

        num_sel = len(self.checked_list)
        old_idx_list = [pw.idx for pw in self.checked_list]
        old_idx_set = set(old_idx_list)
        new_check_list = []
        if shift_pressed:
            if num_sel == 0:
                new_check_list.append(idx)
            else:
                tgt_w = self.pairwidget_list[idx]
                if ctrl_pressed:
                    sel_min, sel_max = (
                        min(old_idx_list[0], tgt_w.idx),
                        max(old_idx_list[-1], tgt_w.idx),
                    )
                else:
                    sel_min, sel_max = (
                        min(self.sel_anchor_widget.idx, tgt_w.idx),
                        max(self.sel_anchor_widget.idx, tgt_w.idx),
                    )
                new_check_list = list(range(sel_min, sel_max + 1))
        elif ctrl_pressed:
            new_check_set = set(old_idx_list)
            if idx in new_check_set:
                new_check_set.remove(idx)
                if (
                    self.sel_anchor_widget is not None
                    and self.sel_anchor_widget.idx == idx
                ):
                    self.sel_anchor_widget = None
            elif checked:
                new_check_set.add(idx)
            new_check_list = list(new_check_set)
            new_check_list.sort()
            if checked:
                self.sel_anchor_widget = self.pairwidget_list[idx]
        else:
            if num_sel > 2:
                if idx in old_idx_set:
                    old_idx_set.remove(idx)
                    checked = True
            if checked:
                new_check_list.append(idx)

        new_check_set = set(new_check_list)
        check_changed = False
        for oidx in old_idx_set:
            if oidx not in new_check_set:
                self.pairwidget_list[oidx]._set_checked_state(False)
                check_changed = True

        self.checked_list.clear()
        for nidx in new_check_list:
            pw = self.pairwidget_list[nidx]
            if nidx not in old_idx_set:
                check_changed = True
                pw._set_checked_state(True)
            self.checked_list.append(pw)

        num_new = len(new_check_list)
        if num_new == 0:
            self.sel_anchor_widget = None
        elif num_new == 1 or self.sel_anchor_widget is None:
            self.sel_anchor_widget = self.checked_list[0]
        if check_changed:
            self.selection_changed.emit()
            if pwc.checked:
                pwc.e_trans.focus_in.emit(pwc.idx)

    def set_selected_list(self, selection_indices: List):
        self.clearDrag()

        old_sel_set, new_sel_set = (
            set([pw.idx for pw in self.checked_list]),
            set(selection_indices),
        )
        to_remove = old_sel_set.difference(new_sel_set)
        to_add = new_sel_set.difference(old_sel_set)
        self.sel_anchor_widget = None

        for idx in to_remove:
            pw = self.pairwidget_list[idx]
            pw._set_checked_state(False)
            self.checked_list.remove(pw)

        for ii, idx in enumerate(to_add):
            pw = self.pairwidget_list[idx]
            pw._set_checked_state(True)
            self.checked_list.append(pw)
            if ii == 0:
                self.sel_anchor_widget = pw

    def clearAllSelected(self, emit_signal=True):
        self.sel_anchor_widget = None
        if len(self.checked_list) > 0:
            for w in self.checked_list:
                w._set_checked_state(False)
            self.checked_list.clear()
            if emit_signal:
                self.selection_changed.emit()

    def removeWidget(self, widget: TransPairWidget, remove_checked: bool = True):
        widget.setVisible(False)
        if remove_checked:
            if (
                self.sel_anchor_widget is not None
                and self.sel_anchor_widget.idx == widget.idx
            ):
                self.sel_anchor_widget = None
            if widget in self.checked_list:
                widget._set_checked_state(False)
                self.checked_list.remove(widget)
        self.vlayout.removeWidget(widget)

    def focusOutEvent(self, e: QFocusEvent) -> None:
        self.focus_out.emit()
        super().focusOutEvent(e)

    def setSourceVisible(self, show: bool):
        self.source_visible = show
        for pw in self.pairwidget_list:
            pw.e_source.setVisible(show)

    def setTransVisible(self, show: bool):
        self.trans_visible = show
        for pw in self.pairwidget_list:
            pw.e_trans.setVisible(show)
