from typing import List

from qtpy.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Property,
    Signal,
)
from qtpy.QtGui import (
    QColor,
    QFocusEvent,
    QGuiApplication,
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
    QGraphicsEffect,
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
    变量（ui/misc.py::get_theme_color），随主题即时取色。不读任何实例
    状态（PyQt 复活出空壳实例也照画不误）。
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


class _DragDim(QWidget):
    """拖拽变暗遮罩：自绘一层半透明黑。

    不逐帧改 ``setStyleSheet``（每帧重解析 QSS），也不挂 QGraphicsEffect
    （Python 子类效果的析构/空壳坑见 _CardScaleEffect 注释）；整块覆盖
    scrollContent，一帧一次 fillRect，成本可忽略。类属性兜底（空壳实例
    无实例字典时画不出东西，而不是抛异常）。
    """

    _alpha = 0.0

    def __init__(self, parent: QWidget, alpha: int) -> None:
        super().__init__(parent)
        self._alpha = float(alpha)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        a = int(round(self._alpha))
        if a <= 0:
            return
        QPainter(self).fillRect(self.rect(), QColor(0, 0, 0, min(a, 255)))


class _CardScaleEffect(QGraphicsEffect):
    """拖拽抓住卡的纯绘制层缩放（顶中锚）。

    绘制走 DeviceCoordinates 快照（Qt drop-shadow 同款模式）：快照按
    boundingRectFor 外扩后的边界抓取，resetTransform 后围绕源矩形顶中
    锚做 scale——只改绘制不改几何，layout 与落点指示框零感知；顶中锚
    使堆顶（咬住光标的抓取点）在缩放中保持不动。boundingRect 按最大
    倍率一次性外扩（横向两侧均摊、纵向全在底部），动画改 factor 只需
    update()，无需重报几何。

    快照取设备像素（高 DPI 屏放大后不糊），而 resetTransform 之后
    painter 与 sourcePixmap 的 offset 都回到逻辑坐标（DPR 由 Qt 在绘制
    时施加、pixmap 自带 devicePixelRatio），故锚点须从设备像素换算回逻
    辑坐标，见 draw 内注释。

    缩放动画以效果自身为父、驱动自身的 factor 属性（animate_to）：
    效果被摘除/控件销毁时动画随之析构，回调链上没有任何指向拖拽区
    的引用——引用循环会让拖拽区只能等 GC 拆环，级联析构时重入
    Python 回调触碰已删 C++ 对象直接硬崩（测试套件批量回收 area 引爆）。
    """

    # 类属性兜底（勿删）：setGraphicsEffect 之后效果归 Qt 所有，Python
    # 实例可能先被回收而 C++ 对象仍装在被拖卡上——此后任何一次重绘都会
    # 让 PyQt 用「没走过 __init__ 的空壳」重建包装器，虚拟方法里读实例
    # 属性即 AttributeError，而虚拟回调里抛异常 PyQt 直接 qFatal 硬崩
    # （2026-09-14 全量套件 exit 127 的真因：套件里前序用例的残留效果在
    # 后续用例建卡重绘时被唤醒）。空壳态退化为不缩放、画原图，与
    # factor<=1 同一路径，视觉上等于「效果已释放」。
    _max_factor = 1.0
    _factor = 1.0
    _anim = None
    # 源 pixmap 缓存的空壳兜底（同上）：None = 未缓存，下次 draw 重抓
    _cached_pixmap = None
    _cached_pad = None

    def __init__(self, max_factor: float):
        super().__init__()
        self._max_factor = max_factor
        self._factor = 1.0
        self._anim = None
        self._cached_pixmap = None
        self._cached_pad = None

    def _get_factor(self) -> float:
        return self._factor

    def _set_factor(self, factor: float) -> None:
        self._factor = factor
        self.update()

    factor = Property(float, _get_factor, _set_factor)

    def animate_to(
        self, factor: float, duration: int, on_done=None, dip=None, dip_at=0.3
    ) -> None:
        """把 factor 补间到目标值；*on_done* 不得持有拖拽区（防引用
        循环），且须容忍动画随效果析构时才触发的场景。

        *dip*（可选）在时间轴 *dip_at* 处插一个中间值：抓住时先缩到 *dip*
        再弹到目标（squash→pop）。两端外的过冲不要用——``boundingRectFor``
        只按 GRAB_SCALE 外扩，超出部分会被裁掉两侧。"""
        old = self._anim
        if old is not None:
            try:
                old.stop()  # 同一效果不重定向，仅防重入兜底
            except RuntimeError:
                pass  # 上轮动画已随 DeleteWhenStopped 析构
        anim = QPropertyAnimation(self, b"factor", self)
        anim.setDuration(duration)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(self._factor)
        if dip is not None:
            anim.setKeyValueAt(dip_at, dip)
        anim.setEndValue(factor)
        if on_done is not None:
            anim.finished.connect(on_done)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._anim = anim

    def boundingRectFor(self, src: QRectF) -> QRectF:
        extra_w = src.width() * (self._max_factor - 1.0)
        extra_h = src.height() * (self._max_factor - 1.0)
        return src.adjusted(-extra_w / 2.0, 0.0, extra_w / 2.0, extra_h)

    def draw(self, painter: QPainter) -> None:
        # 恰好 1.0（含还原动画尾帧）直接走原图，省一次像素快照；其余含
        # factor < 1 的按下压缩都走同一放大路径（顶中锚等比即可）
        if abs(self._factor - 1.0) <= 1e-3:
            # 回 1.0 即弃缓存：此后（若有）内容可能已变，下次按新内容重抓
            self._cached_pixmap = None
            self._cached_pad = None
            self.drawSource(painter)
            return
        src = self.sourceBoundingRect()
        # 控件原点在 resetTransform 后参考系（窗口逻辑坐标）里的位置：
        # deviceTransform 映射出设备像素，除掉自身线性缩放（= 屏幕 DPR）
        # 换算回逻辑坐标（同下方锚点的换算）
        dt = painter.deviceTransform()
        origin_dev = dt.map(QPointF(0, 0))
        origin = QPointF(
            origin_dev.x() / (dt.m11() or 1.0), origin_dev.y() / (dt.m22() or 1.0)
        )
        # 源 pixmap 缓存：拖拽期间卡片内容冻结、只做平移，而几何变化会
        # 失效 Qt 对效果源 pixmap 的缓存——没有这层的话跟手补间的每一帧
        # 都要把整张卡（两个 QTextEdit 的富文本排版）重渲到一张设备分辨
        # 率的离屏图，多选拖拽即 N 次/帧，是掉帧的大头。装上后首帧抓一
        # 次，此后每帧只做缩放 blit；抓取/还原动画只改 factor，同一份缓
        # 存全程有效。
        # 注意 offset 是「当帧控件在窗口里的绝对位置」（随卡片移动逐帧变
        # 化），不能直接缓存——直接缓存会把卡片永远画回抓取时刻的位置
        # （被拖卡不跟手、只在损伤区里露出碎片，2026-09-19 实测）。缓存
        # 「pixmap 左上相对控件原点」的常量偏移（PadToEffectiveBoundingRect
        # 的外扩 padding，拖拽期间控件尺寸不变故恒定），每帧用当前原点
        # 重算 offset。
        if self._cached_pixmap is None:
            pixmap, offset = self.sourcePixmap(
                Qt.CoordinateSystem.DeviceCoordinates,
                mode=QGraphicsEffect.PixmapPadMode.PadToEffectiveBoundingRect,
            )
            if pixmap.isNull():
                return
            self._cached_pixmap = pixmap
            # offset 是 QPoint，显式转 QPointF 再做浮点运算（PyQt6 不做
            # QPoint-QPointF 的隐式混算，直接减会在虚拟回调里抛 TypeError
            # 被 PyQt qFatal 成硬崩）
            self._cached_pad = QPointF(offset) - origin
        pixmap = self._cached_pixmap
        offset = origin + self._cached_pad
        # 锚点换算：deviceTransform 映射出的是设备像素，而 resetTransform 之
        # 后 painter 以逻辑坐标绘制（sourcePixmap 的 offset 与 pixmap 的
        # devicePixelRatio 也都是逻辑单位，DPR 由 Qt 绘制时施加），故须除掉
        # 自身的线性缩放（即屏幕 DPR）。混用会把锚点放大 DPR 倍，卡片按其在
        # 屏上的位置成比例偏移：DPR=1 的屏上恒等（看不出），高 DPI 屏上越靠
        # 下偏得越多，且顶行溢出后被裁掉。
        pivot = dt.map(QPointF(src.x() + src.width() / 2.0, src.y()))
        pivot = QPointF(
            pivot.x() / (dt.m11() or 1.0), pivot.y() / (dt.m22() or 1.0)
        )
        painter.save()
        painter.resetTransform()
        painter.translate(pivot)
        painter.scale(self._factor, self._factor)
        painter.translate(-pivot)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(offset, pixmap)
        painter.restore()


def _detach_scale_effect(pw, eff):
    """缩放还原到位后摘除效果（动画结束回调）。

    延迟到事件循环下一轮执行：析构级联里 setGraphicsEffect 重入会
    双删；下一轮时若控件已销毁或效果已被替换/摘除（身份不符）则跳过。
    只持 pw/eff，不持拖拽区。"""

    def _detach():
        try:
            if pw.graphicsEffect() is eff:
                pw.setGraphicsEffect(None)
        except RuntimeError:
            pass  # 控件已销毁（效果随之析构）

    QTimer.singleShot(0, _detach)


def _frame_interval() -> int:
    """手驱动定时器的帧间隔（ms）：沿用仓库惯例（ui/pie_menu.py::_anim_interval
    / ui/configpanel.py::_scroll_interval）——``pcfg.animation_fps`` 优先，
    否则按屏幕刷新率，兜底 16ms（60fps）。"""
    fps = pcfg.animation_fps
    if fps > 0:
        return int(round(1000.0 / fps))
    try:
        app = QGuiApplication.instance()
        screens = app.screens() if app is not None else []
        hz = screens[0].refreshRate() if screens else 0
        if hz <= 0:
            return 16
        return max(8, min(int(round(1000.0 / hz)), 16))
    except Exception:
        return 16


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
    # 抓住卡的放大倍率：纯绘制层效果（_CardScaleEffect 顶中锚），
    # layout 与落点指示框不动；关动画（animation_fps < 0）时不启用。
    # 注意：效果的外扩余量按本值一次性给足（见 _CardScaleEffect
    # .boundingRectFor），倍率若改用带回弹过冲的曲线，余量要一并放大，
    # 否则过冲部分会被裁掉两侧。
    GRAB_SCALE = 1.05
    # 抓住瞬间先下压再弹起（squash→pop，手感上像"被手按了一下"）
    SCALE_PRESS = 0.97
    # 下压段在抓取动画时间轴上的位置（0-1）
    SCALE_PRESS_AT = 0.28
    # 抓取放大总时长（含下压段）与释放还原时长：还原比行落位（SETTLE_MS）
    # 略快，卡片有"落进槽位"的层次感
    SCALE_IN_MS = 160
    SCALE_OUT_MS = 110
    # 位移动画时长：跟手（拖拽中追随光标）要更短才不发飘；落位（让位/
    # 退应）保持从容
    CHASE_MS = 90
    SETTLE_MS = 140
    # 跟手补间重定向死区（px）：光标慢移时目标 y 以 1px 步进变化，不设
    # 死区的话每变 1px 就停旧建新一个 QPropertyAnimation（N 张卡 × 每次
    # 鼠标事件）。在册动画已飞向 ≤ 死区的邻近目标时沿用，最多滞后死区
    # 像素、追手时长内收敛，观感不可辨；只作用于跟手路径，落位（让位/
    # 退应）的目标是精确槽位，不受死区影响
    RETARGET_DEADZONE = 2
    # 多选堆叠逐卡落后步长：聚拢/展开读起来是"码起来"而不是一起飞
    STAGGER_MS = 14
    # 自动滚动：触发边缘距离、速度爬升系数（向目标速度渐变，不猛启猛停）、
    # 停下的速度阈值
    AUTOSCROLL_EDGE = 30
    AUTOSCROLL_RAMP = 0.3
    AUTOSCROLL_MIN_STOP = 0.5

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
        self._pile_grab_dy = 0                       # 堆顶相对光标的内容 y 偏移（居中抓取）
        self._pile_offsets: List[int] = []           # 被拖组折叠偏移（堆顶=0）
        self._pile_parent: QWidget = None            # 拖拽中被拖组的提层父级（主窗口层）
        self._pile_focus_fw = None                   # 提层前的焦点控件（收尾恢复）
        self._drag_dim: QWidget = None
        self._gap_frame: QFrame = None
        self._pos_anims = {}
        # 抓住缩放（_CardScaleEffect）：pw → 在册效果；动画由效果自驱
        self._scale_effects = {}
        self._auto_timer: QTimer = None
        self._auto_speed = 0.0        # 当前滚动速度（px/tick，向目标渐变）
        self._auto_target = 0.0       # 目标滚动速度（按接近边缘程度给）
        self._scroll_acc = 0.0        # 亚像素累加：避免边缘处整像素跳步

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
    # 被拖组拖拽期间提层到主窗口：滚动层级的父边界会裁掉抓住放大的
    # 溢出（Qt 子控件画不出父控件边界），提层后浮在一切之上自由绘制。

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
        # 聚拢锚点 = 触发时鼠标的内容坐标，堆顶卡**纵向居中**于光标：
        # 光标咬住卡片腰部（旧行为是堆顶贴光标、整卡吊在光标下方，拖到
        # 视口下缘时大半张卡已出视野，手感偏且落点指示框与卡片脱节感强）。
        # 各卡从原位聚拢动画飞向光标；此后堆顶以追随补间咬合光标，
        # 抓取卡与光标的相对偏移 _pile_grab_dy 全程恒定
        self._pile_grab_dy = -(drags[0].height() // 2)
        pile_top = (
            int(cursor_vp_y + self.verticalScrollBar().value()) + self._pile_grab_dy
        )
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

        # 变暗遮罩：盖住非拖拽内容，拖拽组与指示框浮在其上保持原生
        # 全分辨率渲染。自绘（不逐帧改 QSS、不挂图形效果）
        self._drag_dim = _DragDim(self.scrollContent, self.DIM_ALPHA)
        self._drag_dim.setGeometry(
            0, 0, self.scrollContent.width(), self.scrollContent.height()
        )
        self._drag_dim.show()

        # 落点指示框（位置随 _apply_arrangement 刷新）：先直接就位，跨行
        # 才走补间——首帧从 (0,0) 飞进来会很怪
        self._gap_frame = _DragGapFrame(self.scrollContent)
        self._gap_frame.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        _, gap_top0 = self._arrange_targets()
        self._gap_frame.setGeometry(
            self._base_x, gap_top0, self._card_w, self._gap_h
        )

        # 提层：被拖组挂到主窗口层。滚动区/视口/内容的父边界会裁掉
        # 放大溢出的部分（Qt 子控件画不出父控件边界），提层后浮在
        # 一切之上，放大溢出自由绘制；坐标在消费处逐帧经
        # _pile_org_in_parent 换算（滚动时 scrollContent 在窗口里移动）。
        # 与缩放同门控：关动画（animation_fps < 0）时既不缩放也不提层，
        # 行为与旧版完全一致。焦点控件先记账：reparent 会把它从卡内
        # 输入框挤掉，收尾恢复。
        if pcfg.animation_fps >= 0:
            self._pile_parent = self.window()
            self._pile_focus_fw = QApplication.focusWidget()
            for w in drags:
                gp = w.mapToGlobal(QPoint(0, 0))
                w.setParent(self._pile_parent)
                w.move(self._pile_parent.mapFromGlobal(gp))
                w.show()
        # 被拖组保持真身可见：聚拢动画飞向光标锚点（逐卡落后 STAGGER_MS，
        # 读起来是"码起来"而不是一起飞）；z 序按原顺序，后位卡在上，
        # 各卡头顶条（含编号徽标）依次可见。
        # 抓住缩放：快速连拖时上一局的还原动画可能仍在飞，先清干净
        # 再装效果并放大（与聚拢飞行同时进行）
        self._clear_scale_effects()
        self._install_scale_effects(drags)
        pile_org_y = self._pile_org_in_parent().y()
        for i, (w, off) in enumerate(zip(drags, self._pile_offsets)):
            self._move_card(
                w,
                pile_org_y + pile_top + off,
                animate=True,
                duration=self.CHASE_MS + i * self.STAGGER_MS,
            )
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
                self._base_x,
                self._gap_frame.y(),
                self._card_w,
                self._gap_h,
            )
            # 指示框走与行同一条补间（跨行时框跟着行滑过去，而不是瞬移
            # 到目标槽位、行还在半路）；登记进同一张 _pos_anims 表，
            # 收尾一并停掉
            self._move_card(self._gap_frame, gap_top, animate)

    def _move_card(
        self,
        pw: TransPairWidget,
        target_y: int,
        animate: bool,
        duration: int = None,
        dead_zone: int = 0,
    ):
        """把卡片补间到目标 y（x 不动）；*duration* 缺省用落位时长。

        目标未变则不重启：高频鼠标事件与连续跨行会反复调用，重启只会
        平白创建动画对象（拖拽中的跟手补间即靠这条合并重定向）。
        *dead_zone* 放宽为「已飞向 ≤ *dead_zone* 的邻近目标则沿用」，只
        给跟手路径用（见 RETARGET_DEADZONE）；落位保持 0 = 精确到位。"""
        if pw.y() == target_y:
            return
        old = self._pos_anims.pop(pw, None)
        if old is not None:
            try:
                if abs(old.endValue().y() - target_y) <= dead_zone:
                    self._pos_anims[pw] = old  # 已在飞向同一目标，留着
                    return
                old.stop()
            except RuntimeError:
                pass
        if not animate or pcfg.animation_fps < 0:
            pw.move(pw.x(), target_y)
            return
        anim = QPropertyAnimation(pw, b"pos", self)
        anim.setDuration(self.SETTLE_MS if duration is None else duration)
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

    # ── 抓住缩放：纯绘制层放大/还原（几何零感知）────────────────
    # 动画由效果自身驱动（见 _CardScaleEffect.animate_to），area 只
    # 登记「装着缩放效果」的卡片：还原一开始即出册，效果自驱还原并
    # 在动画结束后自行摘除——登记表与效果/动画之间无引用循环。

    def _install_scale_effects(self, pws):
        """抓住：被拖组装上缩放效果并放大到 GRAB_SCALE（关动画不装）；
        先下压再弹起（squash→pop），手上有「被抓住」的实感。"""
        if pcfg.animation_fps < 0:
            return
        for pw in pws:
            eff = _CardScaleEffect(self.GRAB_SCALE)
            pw.setGraphicsEffect(eff)
            self._scale_effects[pw] = eff
            eff.animate_to(
                self.GRAB_SCALE,
                self.SCALE_IN_MS,
                dip=self.SCALE_PRESS,
                dip_at=self.SCALE_PRESS_AT,
            )

    def _release_scale_effects(self):
        """放下/取消：缩放还原（与退应飞行并行，略快于落位时长），效果
        出册并自驱缩回、动画结束后自行摘除。"""
        for pw, eff in list(self._scale_effects.items()):
            self._scale_effects.pop(pw, None)
            try:
                eff.factor
            except RuntimeError:
                continue  # 效果已随控件销毁，无事可做
            eff.animate_to(
                1.0,
                self.SCALE_OUT_MS,
                on_done=lambda pw=pw, eff=eff: _detach_scale_effect(pw, eff),
            )

    def _remove_scale_effect(self, pw):
        """立即摘除缩放效果（重入清理/兜底）；效果析构连带其动画。"""
        self._scale_effects.pop(pw, None)
        try:
            if pw.graphicsEffect() is not None:
                pw.setGraphicsEffect(None)
        except RuntimeError:
            pass

    def _clear_scale_effects(self):
        """立即清掉全部缩放效果（重入接管）；已出册、自驱还原中的
        效果不在册，由其自身的延迟摘除回调收尾（身份校验防误删新效果）。"""
        for pw in list(self._scale_effects):
            self._remove_scale_effect(pw)

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

    def _pile_org_in_parent(self) -> QPoint:
        """scrollContent 原点在被拖组提层父级（主窗口层）里的位置。
        逐帧调用：滚轮/自动滚动会移动 scrollContent，映射随之变化。
        未提层（关动画）时返回原点，坐标退化为内容坐标。"""
        if self._pile_parent is None:
            return QPoint(0, 0)
        return self.scrollContent.mapTo(self._pile_parent, QPoint(0, 0))

    def _update_drag_frame(self):
        sb = self.verticalScrollBar()
        y_content = int(self._drag_cursor_vp_y + sb.value())
        pile_org_y = self._pile_org_in_parent().y()
        for w, off in zip(self._drag_pws, self._pile_offsets):
            # 追随式跟随：拖拽组永远朝光标做补间（跟手用 CHASE_MS，比落位
            # 短，抓着才不发飘），鼠标每动一次重定目标——聚拢动画天然可见，
            # 任何时刻都不瞬移；目标未变则不重启（_move_card 内合并重定向，
            # 防高频鼠标事件反复创建动画对象）。让位判定用光标坐标
            # （_update_gap），不受视觉滞后影响。ty 为提层父级坐标：内容 y
            # 经 _pile_org_in_parent 换算；_pile_grab_dy 是抓取时的居中
            # 偏移（见 begin_rows_drag），拖拽全程恒定。
            ty = pile_org_y + y_content + self._pile_grab_dy + off
            self._move_card(
                w, ty, animate=True, duration=self.CHASE_MS,
                dead_zone=self.RETARGET_DEADZONE,
            )
        self._update_gap(y_content)
        # 视口边缘自动滚动：把"接近边缘的程度"折算成目标速度（越近越快），
        # 实际速度逐 tick 向目标渐变——起步/停下都不再是硬开关
        vp_h = self.viewport().height()
        edge = self.AUTOSCROLL_EDGE
        if self._drag_cursor_vp_y < edge:
            self._auto_target = -max(3.0, (edge - self._drag_cursor_vp_y) * 0.25)
        elif self._drag_cursor_vp_y > vp_h - edge:
            self._auto_target = max(
                3.0, (self._drag_cursor_vp_y - (vp_h - edge)) * 0.25
            )
        else:
            self._auto_target = 0.0
        if self._auto_target and self._auto_timer is None:
            t = QTimer(self)
            t.timeout.connect(self._auto_scroll_tick)
            self._auto_timer = t
            t.start(_frame_interval())
        # 目标为 0 时不在此处停表：让 _auto_scroll_tick 把速度渐降到阈值
        # 再停（否则离缘瞬间的急停仍然突兀）

    def _auto_scroll_tick(self):
        # 向目标速度渐变；目标归零后用更强的衰减收尾（约 60ms 停下，
        # 只多滚几个像素——不是惯性滑行）
        ramp = self.AUTOSCROLL_RAMP if self._auto_target else 0.55
        self._auto_speed += (self._auto_target - self._auto_speed) * ramp
        if not self._auto_target and abs(self._auto_speed) < self.AUTOSCROLL_MIN_STOP:
            self._stop_auto_scroll()
            return
        # 亚像素累加：速度 < 1px/tick 时也能按整数步进滚出去，
        # 边缘处不再每次整像素跳步
        self._scroll_acc += self._auto_speed
        step = int(self._scroll_acc)
        if step:
            self._scroll_acc -= step
            sb = self.verticalScrollBar()
            sb.setValue(sb.value() + step)
        self._update_drag_frame()

    def _stop_auto_scroll(self):
        if self._auto_timer is not None:
            self._auto_timer.stop()
            self._auto_timer.deleteLater()
            self._auto_timer = None
        self._auto_speed = 0.0
        self._auto_target = 0.0
        self._scroll_acc = 0.0

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
        self._stop_auto_scroll()
        self._retire_overlays()
        for anim in list(self._pos_anims.values()):
            try:
                anim.stop()
            except RuntimeError:
                pass
        self._pos_anims = {}
        # 放回滚动内容层：坐标从提层父级映射回内容坐标（落账快照与
        # 退应动画都在内容坐标系里工作）；提层被挤掉的焦点控件物归原主
        if self._pile_parent is not None:
            for w in self._drag_pws:
                gp = w.mapToGlobal(QPoint(0, 0))
                w.setParent(self.scrollContent)
                w.move(self.scrollContent.mapFromGlobal(gp))
                w.show()
            self._pile_parent = None
        fw, self._pile_focus_fw = self._pile_focus_fw, None
        if fw is not None:
            try:
                if not fw.hasFocus() and fw.isVisible():
                    fw.setFocus(Qt.FocusReason.OtherFocusReason)
            except RuntimeError:
                pass
        # 缩放还原与退应飞行并行（动画结束各自摘效果/自清登记）
        self._release_scale_effects()

    def _retire_overlays(self):
        """拖拽收尾：遮罩/指示框隐藏并交给事件循环销毁。"""
        for w in (self._drag_dim, self._gap_frame):
            if w is not None:
                w.hide()
                w.deleteLater()
        self._drag_dim = None
        self._gap_frame = None

    def _finish_drag(self):
        """松手落账：快照当前位置 → 布局按新序归还并同步激活到终态
        → 经 rearrange_blks 即时落账（几何所见即终态，消费端补间自动
        跳过，数据零延迟窗口）→ 把行搬回快照位置，整体退应飞向布局
        终态（被拖组从堆叠展开落槽、rest 行从让位位微调），动画风格
        与拖拽中的位移动画一致。"""
        if not self._drag_active:
            return
        drags = list(self._drag_pws)
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
        self._settle_to_layout(start_ys, unstack=drags)

    def _settle_to_layout(self, start_ys: dict, unstack=()) -> None:
        """退应动画：行从快照位置飞向（已同步激活的）布局终态；
        *unstack*（被拖组）按堆叠顺序逐卡落后 STAGGER_MS——展开读起来是
        一张张码下去；关动画（animation_fps < 0）时保持就地终态不补间。"""
        if pcfg.animation_fps < 0:
            return
        finals = {w: w.y() for w in self.pairwidget_list}
        rank = {w: i for i, w in enumerate(unstack)}
        for w, sy in start_ys.items():
            if sy == finals[w]:
                continue
            w.move(w.x(), sy)
            self._move_card(
                w,
                finals[w],
                animate=True,
                duration=self.SETTLE_MS + rank.get(w, 0) * self.STAGGER_MS,
            )

    def _cancel_drag(self):
        if not self._drag_active:
            return
        drags = list(self._drag_pws)
        self._teardown_drag()
        start_ys = {w: w.y() for w in self.pairwidget_list}
        self._restore_layout(self.pairwidget_list)  # 原顺序原位
        self.vlayout.activate()
        self._drag_pws = []
        self._rest = []
        self._settle_to_layout(start_ys, unstack=drags)

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
                # 拖拽期间吞掉列表内部与被拖组的 hover，防止输入框误亮
                # （鼠标抓取理论上已隔离，此处兜底；被拖组提层后挂在
                # 主窗口下，祖先链不含 scrollContent，需单独识别）
                w = obj if isinstance(obj, QWidget) else None
                while (
                    w is not None
                    and w is not self.scrollContent
                    and w not in self._drag_pws
                ):
                    w = w.parentWidget()
                if w is self.scrollContent or w in self._drag_pws:
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
