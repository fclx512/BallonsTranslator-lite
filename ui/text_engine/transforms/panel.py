"""Expandable controls for composable text transforms (Stage 5 node H).

Local self-development of upstream v1.5.10 ``transforms/panel.py`` +
``transforms/controls`` (both merged into this file — the upstream
controls module was a dead duplicate and was dropped), keeping the exact
session-facing contract the
stage-5 ``TextTransformEditSession`` (``transforms/editor.py``) relies on:

* 8 signals: ``transform_commit_requested`` / ``transform_preview_requested`` /
  ``transform_drag_commit_requested`` / ``transform_preview_canceled`` /
  ``transform_add_requested`` / ``transform_remove_requested`` /
  ``transform_move_requested`` / ``transform_selected``.
* Panel methods: ``set_transform_items`` / ``set_transform`` /
  ``set_active_format`` / ``select_transform`` / ``clear_transform_selection`` /
  ``finish_pending_transform_edits`` / ``cancel_transform_previews`` /
  ``cancel_pending_transform_edits``.

Local differences from upstream:

* Built on the local ``PanelArea`` (``ui/custom_widget/view_panel.py``) and
  ``SizeControlLabel`` drag labels; numeric editors are compact themed
  ``QLineEdit`` (integer editors draw the chevron steppers from the local icon
  set, mirroring the page-range steppers in ``ui/custom_widget``).
* The add menu is text-only — the local icon set has no per-variant SVGs.
* Everything else (value/drag state machine, mixed-selection aggregation,
  section grouping, card selection) is kept structurally identical to
  upstream so behavior stays proven.
"""

import math

from qtpy.QtCore import (
    QCoreApplication,
    QEvent,
    QPoint,
    QRect,
    QSize,
    QTimer,
    Signal,
    Qt,
)
from qtpy.QtGui import QIcon, QKeyEvent, QPainter
from qtpy.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from utils.fontformat import FontFormat, TextTransformState
from ui.custom_widget import GroupFrame, PanelArea, SeparatorWidget, SizeControlLabel
from ui.misc import get_theme_color
from ui.text_engine.transforms.registry import (
    GLYPH_SLANT_CONTROL,
    TEXT_TRANSFORM_VARIANTS,
)


def _icon(name: str) -> QIcon:
    return QIcon(rf"icons/{name}")


class TransformDragLabel(SizeControlLabel):
    """Parameter label that starts a value drag on press (Escape aborts)."""

    drag_started = Signal()
    drag_canceled = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self.drag_started.emit()
            super().mousePressEvent(event)
            # This label owns the drag. Letting QLabel ignore the press makes
            # it bubble to the card, which immediately toggles selection off.
            event.accept()
            return
        return super().mousePressEvent(event)

    def abort_drag_session(self):
        self.mouse_pressed = False

    def event(self, event):
        if (
            event.type() == QEvent.Type.ShortcutOverride
            and self.mouse_pressed
            and event.key() == Qt.Key.Key_Escape
        ):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape and self.mouse_pressed:
            self.mouse_pressed = False
            self.drag_canceled.emit()
            event.accept()
            return
        return super().keyPressEvent(event)


class _TransformValueEdit(QLineEdit):
    """Line edit with the transform-panel logical width contract."""

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(56)
        return hint

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(56)
        return hint


class _TransformIntegerEdit(_TransformValueEdit):
    """Integer editor with the same compact chevron steppers as page ranges."""

    step_requested = Signal(int)
    ICON_SIZE = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty('integerStepper', True)
        self.setMouseTracking(True)
        self._hover_button = ''

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setWidth(80)
        return hint

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(80)
        return hint

    def _button_rects(self):
        button_size = 16
        right = self.width() - 4
        y = (self.height() - button_size) // 2
        up_rect = QRect(right - button_size, y, button_size, button_size)
        down_rect = QRect(
            up_rect.left() - button_size - 1,
            y,
            button_size,
            button_size,
        )
        return up_rect, down_rect

    @staticmethod
    def _event_pos(event) -> QPoint:
        if hasattr(event, 'position'):
            return event.position().toPoint()
        return event.pos()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        up_rect, down_rect = self._button_rects()
        for name, rect, icon_name in (
            ('down', down_rect, 'chevron-down.svg'),
            ('up', up_rect, 'chevron-up.svg'),
        ):
            if self._hover_button == name and self.isEnabled():
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(
                    get_theme_color(key="@accentPrimary", alpha=32)
                )
                painter.drawRoundedRect(rect, 3, 3)
            pixmap = _icon(icon_name).pixmap(
                self.ICON_SIZE, self.ICON_SIZE
            )
            painter.drawPixmap(
                rect.center().x() - self.ICON_SIZE // 2,
                rect.center().y() - self.ICON_SIZE // 2,
                pixmap,
            )
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = self._event_pos(event)
            up_rect, down_rect = self._button_rects()
            if up_rect.contains(pos) or down_rect.contains(pos):
                self.step_requested.emit(1 if up_rect.contains(pos) else -1)
                event.accept()
                return
        return super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = self._event_pos(event)
        up_rect, down_rect = self._button_rects()
        hovered = (
            'up'
            if up_rect.contains(pos)
            else 'down'
            if down_rect.contains(pos)
            else ''
        )
        if hovered != self._hover_button:
            self._hover_button = hovered
            self.update()
        return super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover_button:
            self._hover_button = ''
            self.update()
        return super().leaveEvent(event)


class CommittedTransformControl(QWidget):
    """One committed numeric transform editor."""

    IDLE = 'IDLE'
    PENDING_TEXT = 'PENDING_TEXT'
    DRAG_PREVIEW = 'DRAG_PREVIEW'

    commit_requested = Signal(str, object)
    preview_requested = Signal(str, float)
    drag_commit_requested = Signal(str, float)
    preview_canceled = Signal(str)
    user_interacted = Signal()

    def __init__(
        self,
        title: str,
        param_name: str,
        display_factor: float,
        canonical_minimum: float,
        canonical_maximum: float,
        suffix: str,
        drag_step: float,
        parent=None,
        decimals: int = 1,
    ):
        super().__init__(parent)
        self.setObjectName('TextTransformControl')
        if display_factor == 0 or not math.isfinite(display_factor):
            raise ValueError('display_factor must be finite and non-zero')
        if canonical_minimum > canonical_maximum:
            raise ValueError('canonical minimum must not exceed maximum')
        if drag_step <= 0 or not math.isfinite(drag_step):
            raise ValueError('drag_step must be finite and positive')
        self.param_name = param_name
        self.display_factor = float(display_factor)
        self.canonical_minimum = float(canonical_minimum)
        self.canonical_maximum = float(canonical_maximum)
        self.suffix = suffix
        self.drag_step = float(drag_step)
        self.decimals = max(0, int(decimals))
        self.state = self.IDLE
        self._model_value = None
        self._model_values = ()
        self._drag_delta = 0.0
        self._drag_remainder = 0.0

        self.label = TransformDragLabel(
            self,
            direction=0,
            text=title,
            alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self.label.setObjectName('TextTransformParamLabel')
        self.label.setWordWrap(True)
        self.label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        self.editor = (
            _TransformIntegerEdit(self)
            if self.decimals == 0
            else _TransformValueEdit(self)
        )
        self.editor.setObjectName('TextTransformParamEditor')
        self.editor.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.editor.setFixedSize(80 if self.decimals == 0 else 56, 22)
        self.editor.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self.editor.textEdited.connect(self._on_text_edited)
        self.editor.returnPressed.connect(self.commit_pending)
        self.editor.installEventFilter(self)
        if isinstance(self.editor, _TransformIntegerEdit):
            self.editor.step_requested.connect(self._step_integer)

        self.label.drag_started.connect(self._start_drag)
        self.label.size_ctrl_changed.connect(self._move_drag)
        self.label.btn_released.connect(self._finish_drag)
        self.label.drag_canceled.connect(self.cancel_preview)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        layout.addWidget(self.editor)

    def _canonical_to_display(self, value: float) -> float:
        return value * self.display_factor

    def _display_to_canonical(self, value: float) -> float:
        return value / self.display_factor

    def _format(self, canonical_value: float) -> str:
        return (
            f'{self._canonical_to_display(canonical_value):.{self.decimals}f}'
            f'{self.suffix}'
        )

    def _parse(self, text: str) -> float:
        text = text.strip()
        if self.suffix and text.endswith(self.suffix):
            text = text[: -len(self.suffix)].strip()
        canonical_value = self._display_to_canonical(float(text))
        if (
            not math.isfinite(canonical_value)
            or not self.canonical_minimum
            <= canonical_value
            <= self.canonical_maximum
        ):
            raise ValueError
        return 0.0 if canonical_value == 0.0 else canonical_value

    def _restore_display(self):
        self.editor.setText(
            '\N{EM DASH}'
            if self._model_value is None
            else self._format(self._model_value)
        )

    def set_model_value(self, canonical_value, model_values=None):
        self.state = self.IDLE
        self._drag_delta = 0.0
        self._drag_remainder = 0.0
        self._model_value = canonical_value
        self._model_values = (
            (() if canonical_value is None else (canonical_value,))
            if model_values is None
            else tuple(model_values)
        )
        self._restore_display()

    def _on_text_edited(self):
        self.user_interacted.emit()
        if self.state != self.DRAG_PREVIEW:
            self.state = self.PENDING_TEXT

    def commit_pending(self):
        if self.state != self.PENDING_TEXT:
            return False
        try:
            canonical_value = self._parse(self.editor.text())
        except (TypeError, ValueError):
            self.state = self.IDLE
            self._restore_display()
            return False
        self.state = self.IDLE
        self._model_value = canonical_value
        self._model_values = (canonical_value,)
        self._restore_display()
        self.commit_requested.emit(self.param_name, canonical_value)
        return True

    def cancel_pending(self):
        if self.state == self.PENDING_TEXT:
            self.state = self.IDLE
            self._restore_display()

    def eventFilter(self, watched, event):
        if watched is self.editor:
            if (
                event.type() == QEvent.Type.ShortcutOverride
                and event.key() == Qt.Key.Key_Escape
                and self.state == self.PENDING_TEXT
            ):
                event.accept()
                return True
            if (
                event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Escape
            ):
                self.cancel_pending()
                event.accept()
                return True
            if event.type() == QEvent.Type.FocusOut:
                self.commit_pending()
        return super().eventFilter(watched, event)

    def _start_drag(self):
        self.user_interacted.emit()
        self.commit_pending()
        self.state = self.DRAG_PREVIEW
        self._drag_delta = 0.0
        self._drag_remainder = 0.0

    def _drag_limits(self):
        if not self._model_values:
            return None
        canonical_minimum = max(
            self.canonical_minimum - value for value in self._model_values
        )
        canonical_maximum = min(
            self.canonical_maximum - value for value in self._model_values
        )
        limits = (
            self._canonical_to_display(canonical_minimum),
            self._canonical_to_display(canonical_maximum),
        )
        return min(limits), max(limits)

    def _move_drag(self, delta: int):
        if self.state != self.DRAG_PREVIEW:
            self._start_drag()
        movement = float(delta) * self.drag_step
        limits = self._drag_limits()
        if limits is not None and (
            (self._drag_delta <= limits[0] and movement < 0.0)
            or (self._drag_delta >= limits[1] and movement > 0.0)
        ):
            # Discard outward overshoot so reversing responds immediately.
            self._drag_remainder = 0.0
            movement = 0.0
        if self.decimals == 0:
            self._drag_remainder += movement
            whole_steps = math.trunc(self._drag_remainder)
            self._drag_remainder -= whole_steps
            candidate = self._drag_delta + whole_steps
        else:
            candidate = self._drag_delta + movement
        if limits is not None:
            clamped = min(max(candidate, limits[0]), limits[1])
            if clamped != candidate:
                self._drag_remainder = 0.0
            candidate = clamped
        self._drag_delta = candidate
        canonical_delta = self._display_to_canonical(self._drag_delta)
        if self._model_value is None:
            self.editor.setText(
                f'\N{GREEK CAPITAL LETTER DELTA} '
                f'{self._drag_delta:+.1f}{self.suffix}'
            )
        else:
            preview_value = self._model_value + canonical_delta
            self.editor.setText(self._format(preview_value))
        self.preview_requested.emit(
            self.param_name,
            canonical_delta,
        )

    def _finish_drag(self):
        if self.state != self.DRAG_PREVIEW:
            return
        delta = self._drag_delta
        self.state = self.IDLE
        self._drag_delta = 0.0
        self._drag_remainder = 0.0
        self._restore_display()
        if delta == 0.0:
            self.preview_canceled.emit(self.param_name)
        else:
            self.drag_commit_requested.emit(
                self.param_name,
                self._display_to_canonical(delta),
            )

    def cancel_preview(self):
        self.label.abort_drag_session()
        if self.state != self.DRAG_PREVIEW:
            return
        self.state = self.IDLE
        self._drag_delta = 0.0
        self._drag_remainder = 0.0
        self._restore_display()
        self.preview_canceled.emit(self.param_name)

    def _step_integer(self, direction: int):
        self.user_interacted.emit()
        canonical_step = self._display_to_canonical(
            1.0 if direction > 0 else -1.0
        )
        if self.state == self.PENDING_TEXT:
            try:
                canonical_value = self._parse(self.editor.text())
            except (TypeError, ValueError):
                self.cancel_pending()
                return
            canonical_value = min(
                max(canonical_value + canonical_step, self.canonical_minimum),
                self.canonical_maximum,
            )
            self.state = self.IDLE
            self._model_value = canonical_value
            self._model_values = (canonical_value,)
            self._restore_display()
            self.commit_requested.emit(self.param_name, canonical_value)
            return
        if not self._model_values:
            return
        display_delta = self._canonical_to_display(canonical_step)
        limits = self._drag_limits()
        if limits is not None:
            display_delta = min(max(display_delta, limits[0]), limits[1])
        if display_delta:
            canonical_delta = self._display_to_canonical(display_delta)
            self.preview_requested.emit(self.param_name, canonical_delta)
            self.drag_commit_requested.emit(
                self.param_name,
                canonical_delta,
            )


class CommittedTransformChoiceControl(QWidget):
    """One immediately committed transform choice (grid interpolation)."""

    commit_requested = Signal(str, object)
    user_interacted = Signal()

    def __init__(self, title, param_name, choices, parent=None):
        super().__init__(parent)
        self.setObjectName('TextTransformControl')
        self.param_name = param_name
        self.choices = tuple(choices)
        self.label = QLabel(title, self)
        self.label.setObjectName('TextTransformParamLabel')
        self.label.setWordWrap(True)
        self.combobox = QComboBox(self)
        self.combobox.setObjectName('TextTransformParamEditor')
        for value, label in self.choices:
            self.combobox.addItem(label(), value)
        self.combobox.activated.connect(self._commit_index)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        layout.addWidget(self.combobox)

    def _commit_index(self, index):
        self.user_interacted.emit()
        self.commit_requested.emit(
            self.param_name, self.combobox.itemData(index)
        )

    def set_model_value(self, value):
        index = self.combobox.findData(value)
        self.combobox.setCurrentIndex(index)

    def cancel_pending(self):
        pass

    def cancel_preview(self):
        pass

    def commit_pending(self):
        return False


class TransformParameterPanel(QFrame):
    """One indexed transform operation with independently owned controls."""

    commit_requested = Signal(int, str, object)
    preview_requested = Signal(int, str, float)
    drag_commit_requested = Signal(int, str, float)
    preview_canceled = Signal(int, str)
    remove_requested = Signal(int)
    move_requested = Signal(int, int)
    card_clicked = Signal(int)
    selected = Signal(int)

    def __init__(self, index, variant, parent=None):
        super().__init__(parent)
        self.index = int(index)
        self._hovered = False
        self._selected = False
        self.setObjectName('TextTransformParameterPanel')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        self.title_label = QLabel(variant.label(), self)
        self.title_label.setObjectName('TextTransformParameterTitle')
        self.title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        self.move_up_button = QToolButton(self)
        self.move_up_button.setObjectName('TextTransformMoveButton')
        self.move_up_button.setIcon(_icon('chevron-up.svg'))
        self.move_up_button.setToolTip(self.tr('Move Up'))
        self.move_up_button.setAccessibleName(self.tr('Move Up'))
        self.move_up_button.clicked.connect(
            lambda: self.move_requested.emit(self.index, -1)
        )

        self.move_down_button = QToolButton(self)
        self.move_down_button.setObjectName('TextTransformMoveButton')
        self.move_down_button.setIcon(_icon('chevron-down.svg'))
        self.move_down_button.setToolTip(self.tr('Move Down'))
        self.move_down_button.setAccessibleName(self.tr('Move Down'))
        self.move_down_button.clicked.connect(
            lambda: self.move_requested.emit(self.index, 1)
        )

        self.close_button = QToolButton(self)
        self.close_button.setObjectName('TextTransformCloseButton')
        self.close_button.setIcon(_icon('titlebar_close.svg'))
        self.close_button.setToolTip(self.tr('Delete Transform'))
        self.close_button.setAccessibleName(self.tr('Delete Transform'))
        self.close_button.clicked.connect(
            lambda: self.remove_requested.emit(self.index)
        )

        action_widget = QWidget(self)
        action_widget.setObjectName('TextTransformPanelActions')
        action_widget.setFixedWidth(66)
        self.action_widget = action_widget
        action_layout = QHBoxLayout(action_widget)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(4)
        for button in (
            self.move_up_button,
            self.move_down_button,
            self.close_button,
        ):
            button.setFixedSize(18, 18)
            button.setIconSize(QSize(12, 12))
            action_layout.addWidget(button)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)
        header_layout.addWidget(self.title_label)
        header_layout.addWidget(action_widget)

        self.controls = {}
        self.controls_widget = QWidget(self)
        controls_widget = self.controls_widget
        controls_widget.setObjectName('TextTransformPanelControls')
        controls_layout = QVBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(6)
        grouped_controls = {}
        for spec in variant.controls:
            if spec.choices:
                control = CommittedTransformChoiceControl(
                    spec.label(),
                    spec.attribute_name,
                    spec.choices,
                    controls_widget,
                )
            else:
                control = CommittedTransformControl(
                    spec.label(),
                    spec.attribute_name,
                    spec.factor,
                    spec.minimum,
                    spec.maximum,
                    spec.suffix,
                    0.125 if spec.decimals == 0 else 1.0,
                    controls_widget,
                    decimals=spec.decimals,
                )
                if spec.shortcut is not None:
                    shortcut = spec.shortcut()
                    control.label.setToolTip(shortcut)
                    control.editor.setToolTip(shortcut)
            control.layout().setSpacing(8)
            control.label.setWordWrap(False)
            control.label.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            editor = (
                control.combobox
                if isinstance(control, CommittedTransformChoiceControl)
                else control.editor
            )
            editor.setProperty('cardEditor', True)
            editor.setFixedHeight(22)
            control.commit_requested.connect(
                lambda name, value, self=self:
                self.commit_requested.emit(self.index, name, value)
            )
            control.user_interacted.connect(
                lambda self=self: self.selected.emit(self.index)
            )
            if isinstance(control, CommittedTransformControl):
                control.preview_requested.connect(
                    lambda name, value, self=self:
                    self.preview_requested.emit(self.index, name, value)
                )
                control.drag_commit_requested.connect(
                    lambda name, value, self=self:
                    self.drag_commit_requested.emit(self.index, name, value)
                )
                control.preview_canceled.connect(
                    lambda name, self=self:
                    self.preview_canceled.emit(self.index, name)
                )
            self.controls[spec.attribute_name] = control
            section = spec.section() if spec.section is not None else None
            grouped_controls.setdefault(section, []).append((spec, control))

        section_order = ([None] if None in grouped_controls else []) + [
            section for section in grouped_controls if section is not None
        ]
        self.section_labels = []
        self._section_controls_data = []
        for section in section_order:
            if section is not None:
                section_label = QLabel(section, controls_widget)
                section_label.setObjectName('TextTransformSectionTitle')
                controls_layout.addWidget(section_label)
                self.section_labels.append(section_label)
            grid = QGridLayout()
            grid.setContentsMargins(4 if section is not None else 0, 0, 0, 0)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(4)
            section_controls = grouped_controls[section]
            max_columns = max(
                spec.section_columns for spec, _control in section_controls
            )
            self._section_controls_data.append({
                'section': section,
                'controls': [control for _spec, control in section_controls],
                'max_columns': min(max_columns, len(section_controls)),
                'grid': grid,
                'column_count': 0,
            })
            controls_layout.addLayout(grid)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 12, 8)
        layout.setSpacing(6)
        layout.addLayout(header_layout)
        layout.addWidget(controls_widget)

        self._sync_action_visibility()

    def _apply_section_columns(self, section_data: dict, column_count: int) -> None:
        """Place the section's controls into the grid using ``column_count`` columns."""
        controls = section_data['controls']
        grid = section_data['grid']
        old_count = section_data['column_count']
        if old_count == column_count and grid.count() > 0:
            return
        for col in range(old_count):
            grid.setColumnStretch(col, 0)
        for col in range(column_count):
            grid.setColumnStretch(col, 1)
        for control in controls:
            grid.removeWidget(control)
        single_column = column_count == 1
        for control_index, control in enumerate(controls):
            editor = (
                control.combobox
                if isinstance(control, CommittedTransformChoiceControl)
                else control.editor
            )
            if single_column:
                control.layout().setStretch(0, 1)
                control.layout().setStretch(1, 0)
                editor.setSizePolicy(
                    QSizePolicy.Policy.Fixed,
                    QSizePolicy.Policy.Fixed,
                )
            else:
                control.layout().setStretch(0, 0)
                control.layout().setStretch(1, 1)
                editor.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Fixed,
                )
            control.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Preferred,
            )
            grid.addWidget(
                control,
                control_index // column_count,
                control_index % column_count,
            )
        grid.activate()
        section_data['column_count'] = column_count

    def _relayout_for_width(self, width: int) -> None:
        """Choose the largest column count that still fits each section."""
        if width <= 0 or not self._section_controls_data:
            return
        # The controls widget fills the card width minus the outer margins
        # (8 left, 12 right) and the grid's own left margin.
        base_width = max(1, width - 20)
        for section_data in self._section_controls_data:
            grid = section_data['grid']
            controls = section_data['controls']
            max_columns = section_data['max_columns']
            if max_columns <= 1 or len(controls) <= 1:
                self._apply_section_columns(section_data, 1)
                continue
            available = base_width - grid.contentsMargins().left()
            spacing = grid.horizontalSpacing()
            min_required = max(
                control.minimumSizeHint().width() for control in controls
            )
            optimal = 1
            for columns in range(max_columns, 0, -1):
                needed = columns * min_required + (columns - 1) * spacing
                if needed <= available:
                    optimal = columns
                    break
            self._apply_section_columns(section_data, optimal)

    def set_index(self, index: int) -> None:
        self.index = int(index)

    def set_move_enabled(self, can_move_up: bool, can_move_down: bool) -> None:
        self.move_up_button.setEnabled(can_move_up)
        self.move_down_button.setEnabled(can_move_down)

    def set_selected(self, selected: bool) -> None:
        selected = bool(selected)
        if self._selected == selected:
            return
        self._selected = selected
        self.setProperty('selected', selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_values(self, transforms) -> None:
        for name, control in self.controls.items():
            values = [getattr(transform, name) for transform in transforms]
            common = (
                values[0]
                if values and all(value == values[0] for value in values)
                else None
            )
            if isinstance(control, CommittedTransformControl):
                control.set_model_value(common, values)
            else:
                control.set_model_value(common)

    def iter_controls(self):
        return self.controls.values()

    def cancel_pending(self) -> None:
        for control in self.controls.values():
            control.cancel_pending()

    def _sync_action_visibility(self) -> None:
        self.action_widget.setVisible(self._hovered)
        for button in (
            self.move_up_button,
            self.move_down_button,
            self.close_button,
        ):
            button.setVisible(self._hovered)

    def enterEvent(self, event):
        self._hovered = True
        self._sync_action_visibility()
        return super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._sync_action_visibility()
        return super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.card_clicked.emit(self.index)
        return super().mousePressEvent(event)


class TextTransformPanel(PanelArea):
    """Own the transform settings shown under one expandable title."""

    transform_commit_requested = Signal(int, str, object)
    transform_preview_requested = Signal(int, str, float)
    transform_drag_commit_requested = Signal(int, str, float)
    transform_preview_canceled = Signal(int, str)
    transform_add_requested = Signal(str)
    transform_remove_requested = Signal(int)
    transform_move_requested = Signal(int, int)
    transform_selected = Signal(int)

    MAX_CONTENT_HEIGHT = 480

    def __init__(
        self,
        panel_name: str,
        config_name: str,
        config_expand_name: str,
    ):
        super().__init__(panel_name, config_name, config_expand_name)
        self._base_width_hint = 1
        self._syncing_geometry = False
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.scrollContent.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.scrollContent.after_resized.connect(self._sync_content_height)
        self.setMaximumHeight(self.MAX_CONTENT_HEIGHT)

        self.transform_variants = TEXT_TRANSFORM_VARIANTS
        glyph = GLYPH_SLANT_CONTROL
        self.glyph_slant_control = CommittedTransformControl(
            glyph.label(),
            glyph.attribute_name,
            glyph.factor,
            glyph.minimum,
            glyph.maximum,
            glyph.suffix,
            1.0,
            self.scrollContent,
        )
        self.glyph_slant_control.editor.setProperty(
            'glyphSlantEditor', True
        )
        self.glyph_slant_control.editor.setFixedWidth(84)
        self.glyph_slant_control.label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        # Keep it compact beside the Add dropdown: one line, no expanding
        # (otherwise it spreads across the header and centres on the panel).
        self.glyph_slant_control.label.setWordWrap(False)
        self.glyph_slant_control.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self.glyph_slant_control.layout().setSpacing(8)
        self.glyph_slant_control.layout().setStretch(0, 1)
        self.glyph_slant_control.layout().setStretch(1, 2)
        setattr(self, glyph.name, self.glyph_slant_control)
        self.glyph_slant_control.commit_requested.connect(
            lambda name, value:
            self.transform_commit_requested.emit(-1, name, value)
        )
        self.glyph_slant_control.preview_requested.connect(
            lambda name, value:
            self.transform_preview_requested.emit(-1, name, value)
        )
        self.glyph_slant_control.drag_commit_requested.connect(
            lambda name, value:
            self.transform_drag_commit_requested.emit(-1, name, value)
        )
        self.glyph_slant_control.preview_canceled.connect(
            lambda name: self.transform_preview_canceled.emit(-1, name)
        )

        self.add_transform_button = QToolButton(self.scrollContent)
        self.add_transform_button.setObjectName('AddTextTransformButton')
        self.add_transform_button.setText(self.tr('Add'))
        self.add_transform_button.setToolTip(self.tr('Add Transform'))
        self.add_transform_button.setAccessibleName(self.tr('Add Transform'))
        self.add_transform_button.setFixedSize(72, 26)
        self.add_transform_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextOnly
        )
        self.add_transform_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )
        add_menu = QMenu(self.add_transform_button)
        add_menu.setObjectName('TextTransformAddMenu')
        for variant in self.transform_variants:
            action = add_menu.addAction(variant.label())
            action.triggered.connect(
                lambda _checked=False, transform_type=variant.transform_type:
                self.transform_add_requested.emit(transform_type)
            )
        self.add_transform_button.setMenu(add_menu)

        self.transform_mixed_label = QLabel(
            self.tr('Mixed'), self.scrollContent
        )
        self.transform_mixed_label.setObjectName('TextTransformMixedLabel')
        self.transform_mixed_label.setVisible(False)

        self.transform_rows_layout = QVBoxLayout()
        self.transform_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.transform_rows_layout.setSpacing(10)
        self.transform_panels = []
        self._transform_panel_types = ()
        self._selected_transform_index = None

        self.transform_layout = QVBoxLayout()
        self.transform_layout.setContentsMargins(8, 8, 8, 8)
        self.transform_layout.setSpacing(6)
        self.transform_header_layout = QHBoxLayout()
        self.transform_header_layout.setContentsMargins(0, 0, 0, 0)
        self.transform_header_layout.setSpacing(8)
        self.add_transform_layout = QHBoxLayout()
        self.add_transform_layout.setContentsMargins(0, 0, 0, 0)
        self.add_transform_layout.addWidget(
            self.add_transform_button,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )
        # Add dropdown and glyph slant sit together at the top-left, then
        # stretch — never split the two across the full width, which used
        # to push the slant into a centred-looking block.
        self.transform_header_layout.addLayout(self.add_transform_layout)
        self.transform_header_layout.addWidget(self.glyph_slant_control)
        self.transform_header_layout.addStretch()

        self.cards_frame = GroupFrame(self.scrollContent)
        self.cards_frame.setObjectName('TextTransformCardsFrame')
        self.cards_frame.setVisible(False)
        cards_layout = QVBoxLayout(self.cards_frame)
        cards_layout.setContentsMargins(6, 6, 6, 6)
        cards_layout.setSpacing(8)
        cards_layout.addLayout(self.transform_rows_layout)

        self.cards_separator = SeparatorWidget(self.scrollContent)
        self.cards_separator.setObjectName('TextTransformCardsSeparator')
        self.cards_separator.setVisible(False)

        self.transform_layout.addLayout(self.transform_header_layout)
        self.transform_layout.addSpacing(4)
        self.transform_layout.addWidget(self.cards_separator)
        self.transform_layout.addSpacing(4)
        self.transform_layout.addWidget(self.transform_mixed_label)
        self.transform_layout.addWidget(self.cards_frame)
        self.setContentLayout(self.transform_layout)
        self._base_width_hint = super().sizeHint().width()
        self._sync_content_height()
        QTimer.singleShot(0, self._sync_content_height)

    def _sync_content_height(self):
        if self._syncing_geometry:
            return
        self._syncing_geometry = True
        try:
            # The viewport can still report its pre-show size here. Overlay
            # scrollbars consume no layout width, so the frame gives the
            # responsive content width directly.
            content_width = max(
                1, self.width() - 2 * self.frameWidth()
            )
            self.scrollContent.setMinimumWidth(content_width)
            self.scrollContent.setMaximumWidth(content_width)
            self.scrollContent.resize(
                content_width,
                max(1, self.scrollContent.height()),
            )
            self.transform_layout.invalidate()
            content_height = (
                self.transform_layout.heightForWidth(content_width)
                if self.transform_layout.hasHeightForWidth()
                else self.transform_layout.sizeHint().height()
            )
            self.scrollContent.setMinimumHeight(content_height)
            self.scrollContent.resize(
                content_width,
                max(content_height, self.viewport().height()),
            )
            self.transform_layout.activate()
            self.transform_rows_layout.invalidate()
            self.transform_rows_layout.activate()
            # Cards now have their resolved width; reflow internal columns and
            # let the parent layout pick up any resulting height changes.
            for panel in self.transform_panels:
                panel._relayout_for_width(panel.width())
            self.transform_layout.invalidate()
            self.transform_layout.activate()
            self.transform_rows_layout.invalidate()
            self.transform_rows_layout.activate()
            content_height = (
                self.transform_layout.heightForWidth(content_width)
                if self.transform_layout.hasHeightForWidth()
                else self.transform_layout.sizeHint().height()
            )
            self.scrollContent.setMinimumHeight(content_height)
            self.scrollContent.resize(
                content_width,
                max(content_height, self.viewport().height()),
            )
            target = min(
                content_height + 2 * self.frameWidth(),
                self.MAX_CONTENT_HEIGHT,
            )
            self.setMinimumHeight(target)
            self.scrollContent.updateGeometry()
            self.updateGeometry()
            self.view_widget.updateGeometry()
            # A hidden resizable child does not always update QScrollArea's
            # range after its minimum height changes.
            QCoreApplication.sendEvent(
                self, QEvent(QEvent.Type.LayoutRequest)
            )
        finally:
            self._syncing_geometry = False

    def sizeHint(self):
        hint = super().sizeHint()
        if not hasattr(self, 'transform_layout'):
            return hint
        return QSize(
            self._base_width_hint,
            min(
                (
                    self.transform_layout.heightForWidth(
                        max(1, self.width() - 2 * self.frameWidth())
                    )
                    if self.transform_layout.hasHeightForWidth()
                    else self.transform_layout.sizeHint().height()
                )
                + 2 * self.frameWidth(),
                self.MAX_CONTENT_HEIGHT,
            ),
        )

    def _clear_transform_panels(self):
        for panel in self.transform_panels:
            self.transform_rows_layout.removeWidget(panel)
            panel.setParent(None)
            panel.deleteLater()
        self.transform_panels = []
        self._transform_panel_types = ()

    def _rebuild_transform_panels(self, transform_types):
        transform_types = tuple(transform_types)
        if transform_types == self._transform_panel_types:
            return
        self._clear_transform_panels()
        variants = {
            variant.transform_type: variant
            for variant in self.transform_variants
        }
        for index, transform_type in enumerate(transform_types):
            panel = TransformParameterPanel(
                index, variants[transform_type], self.scrollContent
            )
            panel.commit_requested.connect(
                self.transform_commit_requested.emit
            )
            panel.preview_requested.connect(
                self.transform_preview_requested.emit
            )
            panel.drag_commit_requested.connect(
                self.transform_drag_commit_requested.emit
            )
            panel.preview_canceled.connect(
                self.transform_preview_canceled.emit
            )
            panel.remove_requested.connect(
                self.transform_remove_requested.emit
            )
            panel.move_requested.connect(self.transform_move_requested.emit)
            panel.card_clicked.connect(self.toggle_transform)
            panel.selected.connect(self.select_transform)
            self.transform_rows_layout.addWidget(panel)
            self.transform_panels.append(panel)
        self._transform_panel_types = transform_types
        count = len(self.transform_panels)
        for index, panel in enumerate(self.transform_panels):
            panel.set_index(index)
            panel.set_move_enabled(index > 0, index + 1 < count)
            panel.set_selected(index == self._selected_transform_index)
        if (
            self._selected_transform_index is not None
            and self._selected_transform_index >= count
        ):
            self.clear_transform_selection()
        self.cards_frame.setVisible(count > 0)
        self.cards_separator.setVisible(
            self.transform_mixed_label.isVisible() or self.cards_frame.isVisible()
        )
        self._sync_content_height()

    def select_transform(self, index: int, *, emit: bool = True):
        index = int(index)
        if index < 0 or index >= len(self.transform_panels):
            self.clear_transform_selection(emit=emit)
            return
        if self._selected_transform_index == index:
            return
        self._selected_transform_index = index
        for panel_index, panel in enumerate(self.transform_panels):
            panel.set_selected(panel_index == index)
        if emit:
            self.transform_selected.emit(index)

    def toggle_transform(self, index: int):
        if self._selected_transform_index == int(index):
            self.clear_transform_selection()
        else:
            self.select_transform(index)

    def clear_transform_selection(self, *, emit: bool = True):
        if self._selected_transform_index is None:
            return
        self._selected_transform_index = None
        for panel in self.transform_panels:
            panel.set_selected(False)
        if emit:
            self.transform_selected.emit(-1)

    def _set_transform_states(self, states):
        states = [
            state
            if isinstance(state, TextTransformState)
            else TextTransformState(
                state.text_transform, state.glyph_slant_angle
            )
            for state in states
        ]
        glyph_values = [state.glyph_slant_angle for state in states]
        common_glyph = (
            glyph_values[0]
            if glyph_values
            and all(value == glyph_values[0] for value in glyph_values)
            else None
        )
        self.glyph_slant_control.set_model_value(common_glyph, glyph_values)

        sequences = [
            tuple(transform.transform_type for transform in state.stack)
            for state in states
        ]
        common_sequence = (
            sequences[0]
            if sequences
            and all(sequence == sequences[0] for sequence in sequences)
            else None
        )
        mixed = common_sequence is None
        self.transform_mixed_label.setVisible(mixed)
        if mixed:
            self.clear_transform_selection()
            self._rebuild_transform_panels(())
            return
        self._rebuild_transform_panels(common_sequence)
        for index, panel in enumerate(self.transform_panels):
            panel.set_values([state.stack[index] for state in states])
        self._sync_content_height()

    def set_active_format(self, font_format: FontFormat):
        self._set_transform_states([font_format])

    def set_transform_items(self, items):
        self._set_transform_states(
            [
                TextTransformState(
                    item.blk.fontformat.text_transform,
                    item.blk.fontformat.glyph_slant_angle,
                )
                for item in items
            ]
        )

    def set_transform(self, state):
        self._set_transform_states([state])

    def iter_transform_controls(self):
        yield self.glyph_slant_control
        for panel in self.transform_panels:
            yield from panel.iter_controls()

    def cancel_pending_transform_edits(self):
        for control in self.iter_transform_controls():
            control.cancel_pending()

    def cancel_transform_previews(self):
        for control in self.iter_transform_controls():
            control.cancel_preview()

    def finish_pending_transform_edits(self):
        for control in self.iter_transform_controls():
            control.commit_pending()
