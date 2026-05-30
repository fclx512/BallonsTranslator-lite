"""Theme editor dialog with master-detail layout."""

import json
import os
import random
from typing import Dict, List

from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QColor
from qtpy.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from utils import shared as C
from utils.config import pcfg, save_config

from .custom_widget.color_picker import ColorPickerDialog
from .misc import (
    build_stylesheet_from_dict,
    load_custom_themes,
    load_theme_dict,
    parse_stylesheet,
)

PRIMARY_COLOR_KEYS = [
    "@accentPrimary",
    "@accentDetect",
    "@accentOCR",
    "@accentInpaint",
    "@accentTranslate",
    "@highlightColor",
    "@dangerColor",
    "@successColor",
]

COLOR_DISPLAY_NAMES = {
    "@accentPrimary": "Primary Accent",
    "@accentDetect": "Detection",
    "@accentOCR": "OCR",
    "@accentInpaint": "Inpaint",
    "@accentTranslate": "Translate",
    "@highlightColor": "Highlight",
    "@dangerColor": "Danger",
    "@successColor": "Success",
    # Advanced keys
    "@borderColor": "Border",
    "@qwidgetForegroundColor": "Foreground",
    "@qwidgetBackgroundColor": "Widget Background",
    "@widgetBackgroundColor": "Background",
    "@emptyContentBackgroundColor": "Empty Content Background",
    "@bubbleAIBackground": "AI Bubble Background",
    "@titleBarColor": "Title Bar",
    "@pushBtnBackgroundColor": "Button Background",
    "@noboderPushBtnBackgroundColor": "Borderless Button",
    "@transtexteditBackgroundColor": "Text Edit Background",
    "@sliderHandleColor": "Slider Handle",
    "@scrollBarBackground": "Scrollbar Background",
    "@scrollBarColor": "Scrollbar",
    "@scrollBarHoverColor": "Scrollbar Hover",
    "@textColor": "Text",
    "@inputBackgroundColor": "Input Background",
    "@disabledForegroundColor": "Disabled Foreground",
    "@cardBackgroundColor": "Card Background",
    "@hoverBackgroundColor": "Hover Background",
    "@inverseTextColor": "Inverse Text",
    "@accentPrimary20": "Primary Accent 20%",
    "@accentPrimary80": "Primary Accent 80%",
}

ADVANCED_COLOR_KEYS = [
    "@borderColor",
    "@qwidgetForegroundColor",
    "@qwidgetBackgroundColor",
    "@widgetBackgroundColor",
    "@emptyContentBackgroundColor",
    "@bubbleAIBackground",
    "@titleBarColor",
    "@pushBtnBackgroundColor",
    "@noboderPushBtnBackgroundColor",
    "@transtexteditBackgroundColor",
    "@sliderHandleColor",
    "@scrollBarBackground",
    "@scrollBarColor",
    "@scrollBarHoverColor",
    "@textColor",
    "@inputBackgroundColor",
    "@disabledForegroundColor",
    "@cardBackgroundColor",
    "@hoverBackgroundColor",
    "@inverseTextColor",
    "@accentPrimary20",
    "@accentPrimary80",
]

ALL_COLOR_KEYS = PRIMARY_COLOR_KEYS + ADVANCED_COLOR_KEYS


class _ThemeSwatch(QPushButton):
    """A clickable color swatch."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = QColor(0, 0, 0)
        self._hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(25, 25)

    def setColor(self, color: QColor):
        self._color = QColor(color)
        r, g, b, a = color.red(), color.green(), color.blue(), color.alpha()
        if a < 255:
            self.setToolTip(
                f"RGBA({r}, {g}, {b}, {a})  #{r:02x}{g:02x}{b:02x}{a:02x}".upper()
            )
        else:
            self.setToolTip(f"RGB({r}, {g}, {b})  #{r:02x}{g:02x}{b:02x}".upper())
        self.update()

    @property
    def color(self):
        return self._color

    def enterEvent(self, e):
        self._hovered = True
        self.update()
        return super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self.update()
        return super().leaveEvent(e)

    def paintEvent(self, e):
        from qtpy.QtGui import QBrush, QPainter, QPen, QPixmap

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)
        rf = rect.toRectF()

        if self._color.alpha() < 255:
            check = QPixmap(8, 8)
            check.fill(QColor(255, 255, 255))
            cp = QPainter(check)
            cp.fillRect(0, 0, 4, 4, QColor(200, 200, 200))
            cp.fillRect(4, 4, 4, 4, QColor(200, 200, 200))
            cp.end()
            painter.drawTiledPixmap(rect, check)

        painter.setBrush(QBrush(self._color))
        pen_color = (
            QColor(255, 255, 255, 200) if self._hovered else QColor(128, 128, 128, 100)
        )
        painter.setPen(QPen(pen_color, 1.5 if self._hovered else 1))
        painter.drawRoundedRect(rf, 4, 4)
        painter.end()


class ThemeEditorDialog(QDialog):
    """Master-detail theme editor: theme list on the left, color editor on the right."""

    themeSaved = Signal(str)

    def __init__(self, theme_name: str, mainwindow, parent=None):
        super().__init__(parent)
        self._mainwindow = mainwindow
        self._initial_name = theme_name

        self._all_themes = self._load_all()
        self._builtin_names = list(load_theme_dict().keys())

        self._current_name = ""
        self._working_theme: Dict = {}
        self._original_theme: Dict = {}
        self._is_builtin = False
        self._dirty = False

        self._swatches: List[tuple] = []

        self._setup_ui()
        self._select_theme(theme_name)
        self._select_list_item(theme_name)

    # ── Data ─────────────────────────────────────────────────

    def _load_all(self) -> Dict:
        themes = {}
        themes.update(load_theme_dict())
        themes.update(load_custom_themes())
        return themes

    @staticmethod
    def _parse_color(val: str):
        c = QColor(val)
        if c.isValid():
            return c
        return QColor("#000000")

    @staticmethod
    def _color_to_str(c: QColor) -> str:
        """Serialize a QColor to a theme-compatible string, preserving alpha."""
        if c.alpha() < 255:
            return c.name(QColor.NameFormat.HexArgb)
        return c.name()

    # ── UI setup ─────────────────────────────────────────────

    def _setup_ui(self):
        self.setWindowTitle(self.tr("Theme Editor"))
        self.setMinimumSize(800, 550)
        self.resize(820, 600)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left panel: theme list ──
        left = QWidget()
        left.setFixedWidth(200)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 4, 8)
        left_layout.setSpacing(4)

        header = QLabel(self.tr("Themes"))
        f = header.font()
        f.setBold(True)
        header.setFont(f)
        left_layout.addWidget(header)

        self._theme_list = QListWidget()
        self._theme_list.setSpacing(1)
        self._theme_list.setFrameShape(QListWidget.NoFrame)
        self._theme_list.currentRowChanged.connect(self._on_list_selection)
        left_layout.addWidget(self._theme_list, 1)

        # Populate theme list
        self._rebuild_theme_list()

        # New / Delete buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self._new_btn = QPushButton(self.tr("New..."))
        self._new_btn.clicked.connect(self._on_new_theme)
        btn_row.addWidget(self._new_btn)

        self._delete_btn = QPushButton(self.tr("Delete"))
        self._delete_btn.clicked.connect(self._on_delete_theme)
        self._delete_btn.setEnabled(False)
        btn_row.addWidget(self._delete_btn)
        left_layout.addLayout(btn_row)

        root.addWidget(left)

        # ── Separator ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFixedWidth(1)
        root.addWidget(sep)

        # ── Right panel: color editor (scrollable) ──
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 8, 12, 8)
        right_layout.setSpacing(8)

        # Theme name row
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_row.addWidget(QLabel(self.tr("Name:")))
        self._name_edit = QLineEdit()
        self._name_edit.setFixedWidth(220)
        self._name_edit.textChanged.connect(self._on_name_changed)
        name_row.addWidget(self._name_edit)

        self._base_label = QLabel()
        name_row.addWidget(self._base_label)
        name_row.addStretch()

        self._badge = QLabel()
        self._badge.setFixedWidth(50)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._badge.setStyleSheet(
            "QLabel { border-radius: 8px; padding: 2px 6px; font-weight: bold; }"
        )
        name_row.addWidget(self._badge)
        right_layout.addLayout(name_row)

        # "Clone to Edit" button (only for built-in)
        self._clone_btn = QPushButton(self.tr("Clone to Edit"))
        self._clone_btn.setFixedWidth(140)
        self._clone_btn.clicked.connect(self._on_clone)
        self._clone_btn.setVisible(False)
        right_layout.addWidget(self._clone_btn)

        # Scrollable color area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(12)

        # Primary colors
        primary_header = QLabel(self.tr("Theme Colors"))
        f = primary_header.font()
        f.setBold(True)
        primary_header.setFont(f)
        scroll_layout.addWidget(primary_header)

        self._primary_grid = QGridLayout()
        self._primary_grid.setSpacing(4)
        self._primary_grid.setColumnStretch(3, 1)
        self._add_swatch_rows(PRIMARY_COLOR_KEYS, self._primary_grid)
        scroll_layout.addLayout(self._primary_grid)

        # Advanced colors
        adv_header = QLabel(self.tr("Advanced Colors"))
        f = adv_header.font()
        f.setBold(True)
        adv_header.setFont(f)
        scroll_layout.addWidget(adv_header)

        self._advanced_grid = QGridLayout()
        self._advanced_grid.setSpacing(4)
        self._advanced_grid.setColumnStretch(3, 1)
        self._add_swatch_rows(ADVANCED_COLOR_KEYS, self._advanced_grid)
        scroll_layout.addLayout(self._advanced_grid)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        right_layout.addWidget(scroll, 1)

        # Bottom buttons
        btn_box = QDialogButtonBox(self)
        self._apply_btn = btn_box.addButton(
            self.tr("Apply"), QDialogButtonBox.ButtonRole.ApplyRole
        )
        btn_box.addButton(QDialogButtonBox.StandardButton.Ok)
        btn_box.addButton(QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self._on_ok)
        btn_box.rejected.connect(self._on_cancel)
        self._apply_btn.clicked.connect(self._on_apply)
        right_layout.addWidget(btn_box)

        root.addWidget(right, 1)

    def _rebuild_theme_list(self):
        """Rebuild the theme list with built-in and custom sections."""
        self._theme_list.blockSignals(True)
        self._theme_list.clear()

        # Built-in section
        header_item = QListWidgetItem(self.tr("Built-in"))
        header_item.setFlags(Qt.ItemFlag.NoItemFlags)
        f = header_item.font()
        f.setBold(True)
        header_item.setFont(f)
        self._theme_list.addItem(header_item)

        for name in sorted(self._builtin_names):
            item = QListWidgetItem(f"  {name}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._theme_list.addItem(item)

        # Separator
        sep_item = QListWidgetItem("")
        sep_item.setFlags(Qt.ItemFlag.NoItemFlags)
        sep_item.setSizeHint(sep_item.sizeHint() * 0.3)
        self._theme_list.addItem(sep_item)

        # Custom section
        custom_header = QListWidgetItem(self.tr("Custom"))
        custom_header.setFlags(Qt.ItemFlag.NoItemFlags)
        f = custom_header.font()
        f.setBold(True)
        custom_header.setFont(f)
        self._theme_list.addItem(custom_header)

        custom_names = sorted(set(self._all_themes.keys()) - set(self._builtin_names))
        for name in custom_names:
            item = QListWidgetItem(f"  {name}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._theme_list.addItem(item)

        self._theme_list.blockSignals(False)

    def _add_swatch_rows(self, keys, grid):
        for row, key in enumerate(keys):
            display = COLOR_DISPLAY_NAMES.get(key, key)
            name_lbl = QLabel(self.tr(display) if key in COLOR_DISPLAY_NAMES else key)
            name_lbl.setFixedWidth(180)
            grid.addWidget(name_lbl, row, 0)

            swatch = _ThemeSwatch(self)
            swatch.clicked.connect(self._make_on_swatch_clicked(key, swatch))
            grid.addWidget(swatch, row, 1)

            hex_lbl = QLabel()
            hex_lbl.setFixedWidth(90)
            hex_lbl.setStyleSheet("QLabel { font-family: monospace; }")
            grid.addWidget(hex_lbl, row, 2)

            self._swatches.append((key, swatch, hex_lbl))

    def _make_on_swatch_clicked(self, key: str, swatch: _ThemeSwatch):
        def on_clicked():
            if self._is_builtin:
                return
            hex_label = None
            for k, s, hl in self._swatches:
                if s is swatch:
                    hex_label = hl
                    break
            self._open_color_picker(key, swatch, hex_label)

        return on_clicked

    # ── Theme selection ──────────────────────────────────────

    def _select_theme(self, name: str):
        """Load a theme into the editor."""
        if name not in self._all_themes:
            return

        self._current_name = name
        self._is_builtin = name in self._builtin_names
        self._working_theme = dict(self._all_themes[name])
        self._original_theme = dict(self._working_theme)
        self._dirty = False

        base = self._working_theme.get("_base", name)
        self._base_label.setText(self.tr("Based on: ") + base)

        is_dark = "dark" in base.lower()
        if is_dark:
            self._badge.setText(self.tr("Dark"))
            self._badge.setStyleSheet(
                "QLabel { background: #44403C; color: #D6D3D1; border-radius: 8px; padding: 2px 6px; font-weight: bold; }"
            )
        else:
            self._badge.setText(self.tr("Light"))
            self._badge.setStyleSheet(
                "QLabel { background: #E7E5E4; color: #57534E; border-radius: 8px; padding: 2px 6px; font-weight: bold; }"
            )

        # Update UI state
        self._name_edit.blockSignals(True)
        self._name_edit.setText(name)
        self._name_edit.setReadOnly(self._is_builtin)
        self._name_edit.blockSignals(False)

        self._clone_btn.setVisible(self._is_builtin)
        self._delete_btn.setEnabled(not self._is_builtin)
        self._apply_btn.setEnabled(not self._is_builtin)

        # Load colors
        self._refresh_swatches()

    def _refresh_swatches(self):
        for key, swatch, hex_label in self._swatches:
            val = self._working_theme.get(key, "#000000")
            color = self._parse_color(val)
            swatch.setColor(color)
            hex_label.setText(val.upper() if val else "#000000")

    # ── List selection ───────────────────────────────────────

    def _select_list_item(self, name: str):
        """Select a theme in the list by name."""
        for i in range(self._theme_list.count()):
            item = self._theme_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == name:
                self._theme_list.setCurrentRow(i)
                return

    def _on_list_selection(self):
        item = self._theme_list.currentItem()
        if not item:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        if name and name != self._current_name:
            if self._dirty:
                self._save_to_disk()
            self._select_theme(name)

    def _on_name_changed(self, text):
        if self._is_builtin:
            return
        self._dirty = True

    # ── Color picker ─────────────────────────────────────────

    def _open_color_picker(self, key: str, swatch: _ThemeSwatch, hex_label: QLabel):
        current = swatch.color
        pre_edit = self._working_theme.get(key, "")

        dlg = ColorPickerDialog(current, self)
        dlg.colorChanging.connect(lambda c: self._preview_color(key, c, hex_label))

        if dlg.exec_() == QDialog.DialogCode.Accepted:
            new_color = dlg.get_color()
            hex_str = self._color_to_str(new_color)
            self._working_theme[key] = hex_str
            swatch.setColor(new_color)
            hex_label.setText(hex_str.upper())
            self._dirty = True
        else:
            if pre_edit:
                self._working_theme[key] = pre_edit
            self._preview_apply()

    def _preview_color(self, key: str, color: QColor, hex_label: QLabel):
        hex_str = self._color_to_str(color)
        self._working_theme[key] = hex_str
        hex_label.setText(hex_str.upper())
        self._preview_apply()

    def _preview_apply(self):
        stylesheet = build_stylesheet_from_dict(self._working_theme)
        self._mainwindow.setStyleSheet(stylesheet)

    # ── Clone ────────────────────────────────────────────────

    def _on_clone(self):
        """Clone the current built-in theme as a new custom theme."""
        base = self._working_theme.get("_base", self._current_name)
        new_name = f"{base}-custom-{random.randint(100, 999)}"
        while new_name in self._all_themes:
            new_name = f"{base}-custom-{random.randint(100, 999)}"

        custom = load_custom_themes()
        clone = dict(self._working_theme)
        clone["_base"] = base
        custom[new_name] = clone
        self._save_custom_json(custom)
        self._all_themes[new_name] = clone

        self._rebuild_theme_list()
        self._select_list_item(new_name)

    # ── New / Delete ─────────────────────────────────────────

    def _on_new_theme(self):
        """Clone the currently selected theme."""
        if not self._current_name:
            return
        self._on_clone()

    def _on_delete_theme(self):
        if self._is_builtin or not self._current_name:
            return

        reply = QMessageBox.question(
            self,
            self.tr("Delete Theme"),
            self.tr('Delete theme "%s"? This cannot be undone.') % self._current_name,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        custom = load_custom_themes()
        if self._current_name in custom:
            del custom[self._current_name]
            self._save_custom_json(custom)

        self._all_themes.pop(self._current_name, None)
        self._dirty = False

        # Fall back to a built-in theme
        fallback = self._builtin_names[0] if self._builtin_names else ""
        self._rebuild_theme_list()
        self._select_theme(fallback)
        self._select_list_item(fallback)

    # ── Save / Cancel ────────────────────────────────────────

    def _save_to_disk(self):
        if self._is_builtin or not self._current_name:
            return False
        name = self._name_edit.text().strip()
        if not name:
            return False

        custom = load_custom_themes()

        # Handle rename
        if name != self._current_name and self._current_name in custom:
            del custom[self._current_name]

        theme_data = dict(self._working_theme)
        theme_data["_base"] = self._working_theme.get("_base", self._current_name)
        custom[name] = theme_data

        self._save_custom_json(custom)

        # Update tracking
        if name != self._current_name:
            self._all_themes.pop(self._current_name, None)
        self._all_themes[name] = theme_data
        self._current_name = name
        self._is_builtin = False
        self._original_theme = dict(self._working_theme)
        self._dirty = False
        self._rebuild_theme_list()

        # Update active theme config
        is_dark = "dark" in self._working_theme.get("_base", self._current_name).lower()
        if is_dark:
            pcfg.dark_theme = name
        else:
            pcfg.light_theme = name
        return True

    @staticmethod
    def _save_custom_json(data: Dict):
        try:
            os.makedirs(os.path.dirname(C.CUSTOM_THEME_PATH), exist_ok=True)
            with open(C.CUSTOM_THEME_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            QMessageBox.warning(None, "Error", f"Failed to save custom themes: {e}")

    def _on_apply(self):
        if self._dirty and self._save_to_disk():
            save_config()
            self._mainwindow.resetStyleSheet(reverse_icon=True)
            self.themeSaved.emit(self._current_name)
            self._apply_btn.setEnabled(not self._is_builtin)
            self._delete_btn.setEnabled(not self._is_builtin)

    def _on_ok(self):
        self._on_apply()
        self.accept()

    def _on_cancel(self):
        # Restore the currently active theme via full parse_stylesheet flow,
        # which also resets FOREGROUND_FONTCOLOR and SLIDERHANDLE_COLOR globals.
        if self._original_theme:
            theme_name = pcfg.dark_theme if pcfg.darkmode else pcfg.light_theme
            stylesheet = parse_stylesheet(theme_name, reverse_icon=True)
            self._mainwindow.setStyleSheet(stylesheet)
        self.reject()
