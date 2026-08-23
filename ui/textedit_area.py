from typing import List

import numpy as np
from qtpy.QtCore import (
    QEvent,
    QMimeData,
    QPoint,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from qtpy.QtGui import (
    QColor,
    QDrag,
    QDragEnterEvent,
    QDropEvent,
    QFocusEvent,
    QFont,
    QFontMetrics,
    QInputMethodEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPixmap,
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
)

from ui.misc import get_theme_color

from .custom_widget import ScrollBar, Widget
from .textitem import TextBlock

# Styling moved to config/stylesheet.css with dynamic property selectors.
# TransPairWidget card states (checked, hover, drag) are now controlled
# via setProperty() + unpolish/polish, not setStyleSheet().

# Width of the drag handle zone to the right of accent_bar when fold=ON
DRAG_AREA_WIDTH = 22


class SourceTextEdit(QTextEdit):
    hover_enter = Signal(int)
    hover_leave = Signal(int)
    focus_in = Signal(int)
    propagate_user_edited = Signal(int, str, bool)
    ensure_scene_visible = Signal()
    redo_signal = Signal()
    undo_signal = Signal()
    push_undo_stack = Signal(int)
    text_changed = Signal()
    show_select_menu = Signal(QPoint, str)
    focus_out = Signal(int)

    def __init__(self, idx, parent, fold=False, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.idx = idx
        self.pre_editing = False
        self.document().contentsChanged.connect(self.on_content_changed)
        self.document().documentLayout().documentSizeChanged.connect(self.adjustSize)
        self.document().contentsChange.connect(self.on_content_changing)
        self.setAcceptRichText(False)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.old_undo_steps = self.document().availableUndoSteps()
        self.in_redo_undo = False
        self.change_from: int = 0
        self.change_added: int = 0
        self.input_method_from = -1
        self.input_method_text = ""
        self.text_content_changed = False
        self.highlighting = False
        self.paste_flag = False

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
        self.setFold(fold)

    def setFold(self, fold: bool):
        # 编辑/审阅统一：整块高度多行（审阅态不再省高度变单行 NoWrap/35px）
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

    def updateUndoSteps(self):
        self.old_undo_steps = self.document().availableUndoSteps()

    def on_content_changing(self, from_: int, removed: int, added: int):
        if not self.pre_editing:
            self.text_content_changed = True
            if self.hasFocus():
                self.change_from = from_
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
            self.hasFocus()
            and not self.pre_editing
            and not self.highlighting
            and not self.in_acts
        ):
            self.handle_content_change()

    def handle_content_change(self):
        if not self.in_redo_undo:
            change_from = self.change_from
            added_text = ""

            if self.paste_flag:
                self.paste_flag = False
                cursor = self.textCursor()
                cursor.setPosition(change_from)
                cursor.setPosition(
                    self.textCursor().position(), QTextCursor.MoveMode.KeepAnchor
                )
                added_text = cursor.selectedText()

            else:
                if self.input_method_from != -1:
                    added_text = self.input_method_text
                    change_from = self.input_method_from
                    self.input_method_from = -1
                elif self.change_added > 0:
                    cursor = self.textCursor()
                    cursor.setPosition(change_from)
                    cursor.setPosition(
                        change_from + self.change_added, QTextCursor.MoveMode.KeepAnchor
                    )
                    added_text = cursor.selectedText()

            undo_steps = self.document().availableUndoSteps()
            new_steps = undo_steps - self.old_undo_steps
            joint_previous = new_steps == 0
            self.propagate_user_edited.emit(change_from, added_text, joint_previous)
            self.change_added = 0

            if new_steps > 0:
                self.old_undo_steps = undo_steps
                self.push_undo_stack.emit(new_steps)

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
        if self.pre_editing is False:
            cursor = self.textCursor()
            self.input_method_from = cursor.selectionStart()
        if e.preeditString() == "":
            self.pre_editing = False
            self.input_method_text = e.commitString()
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
                self.paste_flag = True
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

    def undo(self) -> None:
        self.in_redo_undo = True
        self.document().undo()
        self.in_redo_undo = False
        self.old_undo_steps = self.document().availableUndoSteps()

    def redo(self) -> None:
        self.in_redo_undo = True
        self.document().redo()
        self.in_redo_undo = False
        self.old_undo_steps = self.document().availableUndoSteps()

    def setPlainTextAndKeepUndoStack(self, text: str):
        cursor = QTextCursor(self.document())
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(text)

    def insert_external_text(self, text: str):
        """Insert text from outside (e.g. QuickSymbolDialog).

        Normal keyboard input relies on the widget having focus to propagate
        changes. When an external dialog inserts text, the widget may not have
        focus, so this method manually sets change tracking state and forces
        propagation to the canvas text item.
        """
        cursor = self.textCursor()
        change_from = cursor.position()
        cursor.insertText(text)
        self.setTextCursor(cursor)
        self.change_from = change_from
        self.change_added = len(text)
        self.handle_content_change()


class TransTextEdit(SourceTextEdit):
    pass


class TransPairWidget(Widget):
    check_state_changed = Signal(object, bool, bool)
    drag_move = Signal(int)
    pw_drop = Signal()

    def __init__(
        self,
        textblock: TextBlock = None,
        idx: int = None,
        fold: bool = False,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.e_source = SourceTextEdit(idx, self, fold)
        self.e_trans = TransTextEdit(idx, self, fold)
        self.textblock = textblock
        self.idx = idx
        self.fold = fold

        # ── Index badges ─────────────────────────────────────────
        # Two badges, one visible at a time depending on fold state.
        # Viewport badge (fold=OFF / Edit mode) — overlaid at top-right
        self.badge_vp = QLabel(self.e_source.viewport())
        self.badge_vp.setObjectName("TextBlockIndexBadge")
        self.badge_vp.setText(str(idx + 1))
        self.badge_vp.setContentsMargins(4, 0, 4, 0)
        self.badge_vp.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge_vp.adjustSize()
        self.badge_vp.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        QTimer.singleShot(0, self._repos_badge_tr)
        # Fade out on source-edit hover so it doesn't block text
        self.e_source.hover_enter.connect(lambda _: self._set_badge_hover(True))
        self.e_source.hover_leave.connect(lambda _: self._set_badge_hover(False))

        # Drag-area badge (fold=ON / 审阅态) — inside the
        # left drag zone
        self.badge_drag = QLabel()
        self.badge_drag.setObjectName("TextBlockIndexBadge")
        self.badge_drag.setText(str(idx + 1))
        self.badge_drag.setContentsMargins(2, 0, 2, 0)
        self.badge_drag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge_drag.adjustSize()
        self.badge_drag.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )

        self.checked = False
        self._is_hovered = False
        self._badge_hovered = False
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

        # Drag handle zone — sits between accent_bar and text content
        # Only visible in fold=ON mode, provides space for drag initiation
        # and contains the drag-area badge (vertically centered via layout).
        self.drag_area = QFrame(self)
        self.drag_area.setObjectName("dragArea")
        self.drag_area.setFixedWidth(DRAG_AREA_WIDTH)
        self.drag_area.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        drag_layout = QVBoxLayout(self.drag_area)
        drag_layout.setContentsMargins(0, 0, 0, 0)
        drag_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drag_layout.addWidget(self.badge_drag)

        hlayout = QHBoxLayout(self)
        hlayout.addWidget(self.accent_bar)
        hlayout.addWidget(self.drag_area)
        hlayout.addLayout(vlayout)
        hlayout.setContentsMargins(0, 0, 0, 0)
        hlayout.setSpacing(0)  # all spacing managed by vlayout margins

        self.setAcceptDrops(True)

        # Apply initial fold layout
        self._apply_fold()

    def setFold(self, fold: bool):
        """Switch badge / drag-column layout per fold state.

            审阅态（fold=ON）与编辑态（fold=OFF）文本框都保持整块多行，
        不再有省高度的单行紧凑样式；差异只在编号徽章位置与拖拽列：
        fold=ON:
          - drag_area 22px visible（左侧拖拽列 + 编号徽章）
          - 编号徽章在 drag_area 内
          - QTextEdit: WidgetWidth + 45px min height
        fold=OFF（编辑态）:
          - drag_area 隐藏
          - 编号徽章在 SourceTextEdit viewport 右上角
          - QTextEdit: WidgetWidth + 45px min height
        """
        if self.fold == fold:
            return
        self.fold = fold
        # setFoldTextarea 只调 pw.setFold，fold 必须传导到文本框，
        # 否则换行/最小高度态丢失（ef0a8d7 引入的回归）
        self.e_source.setFold(fold)
        self.e_trans.setFold(fold)
        self._apply_fold()

    def _apply_fold(self):
        """Update layout and badge visibility for current fold state."""
        if self.fold:
            # ── fold=ON: 审阅态（编号居左 + 拖拽列）──
            self.accent_bar.setFixedWidth(3)
            self.drag_area.show()
            # Set badge style for drag area and ensure sizing
            self.badge_drag.setProperty("folded", True)
            self.badge_drag.style().unpolish(self.badge_drag)
            self.badge_drag.style().polish(self.badge_drag)
            self.badge_drag.adjustSize()
            self.badge_drag.show()
            # Hide viewport badge
            self.badge_vp.hide()
        else:
            # ── fold=OFF: Edit 完整布局 ──
            self.accent_bar.setFixedWidth(3)
            self.drag_area.hide()
            # Show viewport badge at top-right
            self.badge_vp.show()
            # Hide drag badge
            self.badge_drag.hide()
            # Reposition viewport badge
            QTimer.singleShot(0, self._repos_badge_tr)

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if isinstance(e.source(), TransPairWidget):
            e.accept()
        return super().dragEnterEvent(e)

    def handle_drag(self, pos: QPoint):
        y = pos.y()
        to_pos = self.idx
        if y > self.size().height() / 2:
            to_pos += 1
        self.drag_move.emit(to_pos)

    def dragMoveEvent(self, e: QDragEnterEvent) -> None:
        if isinstance(e.source(), TransPairWidget):
            e.accept()
            self.handle_drag(e.position())

        return super().dragMoveEvent(e)

    def dropEvent(self, e: QDropEvent) -> None:
        if isinstance(e.source(), TransPairWidget):
            e.acceptProposedAction()
            self.pw_drop.emit()

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

    def _set_badge_hover(self, hovered: bool):
        """Fade viewport badge opacity on hover (fold=OFF only)."""
        if self.fold:
            return  # drag-area badge doesn't use hover fade
        if self._badge_hovered == hovered:
            return
        self._badge_hovered = hovered
        self.badge_vp.setProperty("hovered", hovered)
        self.badge_vp.style().unpolish(self.badge_vp)
        self.badge_vp.style().polish(self.badge_vp)

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
            self.badge_vp.setText(text)
            self.badge_drag.setText(text)
            self.badge_vp.adjustSize()
            self.badge_drag.adjustSize()
            self._repos_badge_tr()
            self.e_source.idx = idx
            self.e_trans.idx = idx

    def _repos_badge_tr(self):
        """Move viewport badge to top-right corner of SourceTextEdit viewport."""
        try:
            vp = self.e_source.viewport()
            self.badge_vp.move(vp.width() - self.badge_vp.width(), 0)
        except RuntimeError:
            pass


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

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
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
        self.drag: QDrag = None
        self.dragStartPosition = None

        self.source_visible = True
        self.trans_visible = True

        self.drag_to_pos: int = -1

        self.setSizePolicy(
            self.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Expanding
        )
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.RightButton:
            pos = self.mapToGlobal(e.position()).toPoint()
            self.textpanel_contextmenu_requested.emit(pos, True)
        super().mouseReleaseEvent(e)

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.dragStartPosition = e.pos()
        return super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if (
            self.drag is None
            and self.sel_anchor_widget is not None
            and self.dragStartPosition is not None
        ):
            if (
                e.pos() - self.dragStartPosition
            ).manhattanLength() < QApplication.startDragDistance():
                return
            self.dragStartPosition = None
            self.begin_rows_drag(self.sel_anchor_widget)

        return super().mouseMoveEvent(e)

    def begin_rows_drag(self, source_widget: TransPairWidget) -> None:
        """Start the row-reorder drag from *source_widget*."""
        drag = self.drag = QDrag(source_widget)
        mime = QMimeData()
        drag.setMimeData(mime)

        # Drag indicator: "Sel N" badge
        if self.checked_list:
            text = f"Sel {len(self.checked_list)}"
            font = QFont()
            font.setBold(True)
            font.setPixelSize(12)
            fm = QFontMetrics(font)
            tw = fm.horizontalAdvance(text) + 12
            th = fm.height() + 8
            pm = QPixmap(int(tw), int(th))
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(QColor(0, 0, 0, 180))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(0, 0, int(tw), int(th), 4, 4)
            p.setPen(Qt.GlobalColor.white)
            p.setFont(font)
            p.drawText(QRectF(0, 0, tw, th), Qt.AlignmentFlag.AlignCenter, text)
            p.end()
            drag.setPixmap(pm)
            drag.setHotSpot(QPoint(int(tw) // 2, int(th) // 2))

        drag.exec(Qt.DropAction.MoveAction)
        self.drag = None
        if self.drag_to_pos != -1:
            self.set_drag_style(self.drag_to_pos, True)
            self.drag_to_pos = -1

    def set_drag_style(self, pos: int, clear_style: bool = False):
        if pos == len(self.pairwidget_list):
            pos -= 1
            drag_val = "bottom"
        else:
            drag_val = "top"
        if clear_style:
            drag_val = ""
        pw = self.pairwidget_list[pos]
        pw.setProperty("dragPos", drag_val)
        pw.style().unpolish(pw)
        pw.style().polish(pw)

    def clearDrag(self):
        if self.drag_to_pos != -1 and self.drag_to_pos < len(self.pairwidget_list):
            pw = self.pairwidget_list[self.drag_to_pos]
            pw.setProperty("dragPos", "")
            pw.style().unpolish(pw)
            pw.style().polish(pw)
        self.drag_to_pos = -1
        if self.drag is not None:
            try:
                self.drag.cancel()
            except RuntimeError:
                pass
            self.drag = None

    def handle_drag_pos(self, to_pos: int):
        if self.drag_to_pos != to_pos:
            if self.drag_to_pos is not None:
                self.set_drag_style(self.drag_to_pos, True)
            self.drag_to_pos = to_pos
            self.set_drag_style(to_pos)

    def on_pw_dropped(self):
        if self.drag_to_pos != -1:
            to_pos = self.drag_to_pos
            self.drag_to_pos = -1
            self.drag = None
            self.set_drag_style(to_pos, True)
            num_pw = len(self.pairwidget_list)
            num_drags = len(self.checked_list)
            if num_pw < 2 or num_drags == num_pw:
                return

            tgt_pos = to_pos
            drags = []
            for pw in self.checked_list:
                if pw.idx < tgt_pos:
                    tgt_pos -= 1
                drags.append(pw.idx)
            new_pos = np.arange(num_drags, dtype=np.int32) + tgt_pos
            drags = np.array(drags).astype(np.int32)
            new_maps = np.where(drags != new_pos)
            if len(new_maps) == 0:
                return

            drags_ori, drags_tgt = drags[new_maps], new_pos[new_maps]
            result_list = list(range(len(self.pairwidget_list)))
            to_insert = []
            for ii, src_idx in enumerate(drags_ori):
                pos = src_idx - ii
                to_insert.append(result_list.pop(pos))
            for ii, tgt_idx in enumerate(drags_tgt):
                result_list.insert(tgt_idx, to_insert[ii])
            drags_ori, drags_tgt = [], []
            for ii, idx in enumerate(result_list):
                if ii != idx:
                    drags_ori.append(idx)
                    drags_tgt.append(ii)

            self.rearrange_blks.emit((drags_ori, drags_tgt))

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
        if self.drag is not None:
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

    def setFoldTextarea(self, fold: bool):
        """Propagate fold state to all TransPairWidget children."""
        for pw in self.pairwidget_list:
            pw.setFold(fold)

    def setSourceVisible(self, show: bool):
        self.source_visible = show
        for pw in self.pairwidget_list:
            pw.e_source.setVisible(show)

    def setTransVisible(self, show: bool):
        self.trans_visible = show
        for pw in self.pairwidget_list:
            pw.e_trans.setVisible(show)
