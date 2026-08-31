"""Reusable FontFormat field editors grouped per FIELD_GROUPS.

样式管理器右栏（阶段 3）与查找替换格式条件编辑器（阶段 4）共用的控件层
（设计见 docs/技术实现/查找替换与样式管理器重构_设计方案.md §5.2/§6）：

* ``FormatEditorPanel`` — 四组 ``FormatGroupCard``（文本/颜色与描边/排版/效果），
  可编辑字段清单来自 ``utils/style_query.py::FIELD_GROUPS``。效果组（阴影/渐变/
  变换/斜切）为只读摘要徽标——详细配置占空间且交互低效，只笼统标记使用了哪些
  高级效果；保存/应用样式时效果字段原样透传，不做编辑（§7 效果栈迁移预留缝）。
* 变更收集语义与旧 ``StyleDetail._collect_changed`` 一致：编辑值与基线
  FontFormat 逐字段量化 diff（``utils/base_styles.py::quantize_field``），
  未变化的字段不进 patch。
* 字段标签表 ``FIELD_LABELS`` / 分组标题 ``GROUP_TITLES`` 为模块级字面量，
  在定义处用 QCoreApplication.translate 显式标注上下文（i18n 规则，
  禁止 self.tr(variable) 间接查表）。

v1 效果组为只读摘要（阴影/渐变/变换/斜切），不参与变更收集。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from qtpy.QtCore import QCoreApplication, Qt, Signal
from qtpy.QtGui import QColor, QFont, QFontDatabase
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from utils import shared
from utils.base_styles import copy_value, quantize_field
from utils.style_query import FIELD_GROUPS

from .custom_widget import ColorPickerDialog, ColorSwatchBtn

# ── 字段标签 / 分组标题（模块级字面量，定义处显式标注上下文）────────────

GROUP_TITLES: Dict[str, str] = {
    "text": QCoreApplication.translate("StyleFormatEditor", "Text"),
    "color": QCoreApplication.translate("StyleFormatEditor", "Color & Stroke"),
    "layout": QCoreApplication.translate("StyleFormatEditor", "Layout"),
    "effects": QCoreApplication.translate("StyleFormatEditor", "Effects"),
}

FIELD_LABELS: Dict[str, str] = {
    "font_family": QCoreApplication.translate("StyleFormatEditor", "Font Family"),
    "font_size": QCoreApplication.translate("StyleFormatEditor", "Font Size"),
    "font_weight": QCoreApplication.translate("StyleFormatEditor", "Font Weight"),
    "italic": QCoreApplication.translate("StyleFormatEditor", "Italic"),
    "underline": QCoreApplication.translate("StyleFormatEditor", "Underline"),
    "strikeout": QCoreApplication.translate("StyleFormatEditor", "Strikeout"),
    "frgb": QCoreApplication.translate("StyleFormatEditor", "Text Color"),
    "srgb": QCoreApplication.translate("StyleFormatEditor", "Stroke Color"),
    "stroke_width": QCoreApplication.translate("StyleFormatEditor", "Stroke Width"),
    "opacity": QCoreApplication.translate("StyleFormatEditor", "Opacity"),
    "alignment": QCoreApplication.translate("StyleFormatEditor", "Alignment"),
    "vertical": QCoreApplication.translate("StyleFormatEditor", "Vertical"),
    "line_spacing": QCoreApplication.translate("StyleFormatEditor", "Line Spacing"),
    "line_spacing_type": QCoreApplication.translate("StyleFormatEditor", "Line Spacing Type"),
    "letter_spacing": QCoreApplication.translate("StyleFormatEditor", "Letter Spacing"),
    "ligature_common": QCoreApplication.translate("StyleFormatEditor", "Common Ligatures"),
    "ligature_discretionary": QCoreApplication.translate("StyleFormatEditor", "Discretionary Ligatures"),
    "ligature_contextual": QCoreApplication.translate("StyleFormatEditor", "Contextual Ligatures"),
    "oldstyle_nums": QCoreApplication.translate("StyleFormatEditor", "Oldstyle Numerals"),
    "standard_vertical_roman_alignment": QCoreApplication.translate("StyleFormatEditor", "Vertical Roman Alignment"),
    "glyph_slant_angle": QCoreApplication.translate("StyleFormatEditor", "Glyph Slant"),
}

# 效果组只读摘要字段（不提供编辑控件）：保存/应用样式时原样透传。
EFFECT_FIELDS: Tuple[str, ...] = FIELD_GROUPS["effects"]


def effects_tokens(ffmt, only_fields=None) -> List[str]:
    """笼统标记 *ffmt* 使用了哪些高级效果（阴影/渐变/变换/斜切）。

    *only_fields*（变体模式）限定渲染字段：没有任何效果字段被覆盖时不展示。
    激活语义与预览 chips 一致：阴影=radius>0，渐变=enabled，变换=栈非空，
    斜切=角度非零。
    """
    if only_fields is not None and not (set(EFFECT_FIELDS) & set(only_fields)):
        return []
    tokens: List[str] = []
    if getattr(ffmt, "shadow_radius", 0) > 0:
        tokens.append(QCoreApplication.translate("StyleFormatEditor", "Shadow"))
    if getattr(ffmt, "gradient_enabled", False):
        tokens.append(QCoreApplication.translate("StyleFormatEditor", "Gradient"))
    stack = getattr(ffmt, "text_transform", None)
    try:
        n = len(stack)
    except TypeError:
        n = 0
    if n:
        tokens.append(
            QCoreApplication.translate("StyleFormatEditor", "{n} transform(s)").format(n=n)
        )
    if getattr(ffmt, "glyph_slant_angle", 0):
        tokens.append(field_label("glyph_slant_angle"))
    return tokens

# 枚举型字段的取值表：显示文本（翻译）→ 原始值。在定义处显式翻译。
_ENUM_CHOICES: Dict[str, List[Tuple[str, Any]]] = {
    "alignment": [
        (QCoreApplication.translate("StyleFormatEditor", "Left"), 0),
        (QCoreApplication.translate("StyleFormatEditor", "Center"), 1),
        (QCoreApplication.translate("StyleFormatEditor", "Right"), 2),
    ],
    "line_spacing_type": [
        (QCoreApplication.translate("StyleFormatEditor", "Proportional"), 0),
        (QCoreApplication.translate("StyleFormatEditor", "Distance"), 1),
    ],
    "ligature_common": [
        (QCoreApplication.translate("StyleFormatEditor", "Font Default"), "default"),
        (QCoreApplication.translate("StyleFormatEditor", "Enabled"), "enabled"),
        (QCoreApplication.translate("StyleFormatEditor", "Disabled"), "disabled"),
    ],
    "ligature_discretionary": [
        (QCoreApplication.translate("StyleFormatEditor", "Font Default"), "default"),
        (QCoreApplication.translate("StyleFormatEditor", "Enabled"), "enabled"),
        (QCoreApplication.translate("StyleFormatEditor", "Disabled"), "disabled"),
    ],
    "ligature_contextual": [
        (QCoreApplication.translate("StyleFormatEditor", "Font Default"), "default"),
        (QCoreApplication.translate("StyleFormatEditor", "Enabled"), "enabled"),
        (QCoreApplication.translate("StyleFormatEditor", "Disabled"), "disabled"),
    ],
    "oldstyle_nums": [
        (QCoreApplication.translate("StyleFormatEditor", "Font Default"), "default"),
        (QCoreApplication.translate("StyleFormatEditor", "Enabled"), "enabled"),
        (QCoreApplication.translate("StyleFormatEditor", "Disabled"), "disabled"),
    ],
}


def field_label(fname: str) -> str:
    return FIELD_LABELS.get(fname, fname)


# ── 单字段编辑器 ────────────────────────────────────────────────────────


def _spin(**kw) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(kw.get("lo", 0.0), kw.get("hi", 999.0))
    spin.setDecimals(kw.get("dec", 1))
    spin.setSingleStep(kw.get("step", 1.0))
    if kw.get("suffix"):
        spin.setSuffix(kw["suffix"])
    return spin


# 每字段控件规格：工厂构造 (widget, getter, setter, wire)。
# getter/setter 操作原始 FontFormat 值类型；wire 把控件的变更信号接到回调。
def _build_control(fname: str) -> Tuple[QWidget, Callable, Callable, Callable]:
    if fname in ("frgb", "srgb"):
        btn = ColorSwatchBtn()
        btn.setFixedSize(24, 24)
        label = QLabel()
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(btn)
        lay.addWidget(label)
        lay.addStretch()

        def getter():
            return list(btn.color().getRgb()[:3])

        def setter(v):
            c = QColor(*(int(round(x)) for x in v[:3]))
            btn.blockSignals(True)
            btn.setColor(c)
            btn.blockSignals(False)
            label.setText(f"#{c.red():02X}{c.green():02X}{c.blue():02X}")

        def wire(cb):
            def _pick():
                dlg = ColorPickerDialog(btn.color(), btn.window())
                if dlg.exec_() == QDialog.DialogCode.Accepted:
                    c = dlg.get_color()
                    setter([c.red(), c.green(), c.blue()])
                    cb()  # setter 屏蔽了 colorChanged，此处手动通知

            btn.clicked.connect(_pick)

        return w, getter, setter, wire

    if fname in _ENUM_CHOICES:
        combo = QComboBox()
        for text, raw in _ENUM_CHOICES[fname]:
            combo.addItem(text, raw)

        def getter():
            return combo.currentData()

        def setter(v):
            combo.blockSignals(True)
            idx = combo.findData(v)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

        def wire(cb):
            combo.currentIndexChanged.connect(lambda _i: cb())

        return combo, getter, setter, wire

    if fname in ("italic", "underline", "strikeout", "vertical",
                 "standard_vertical_roman_alignment"):
        cb = QCheckBox()
        cb.setObjectName("ConfigCheckBox")

        def getter():
            return cb.isChecked()

        def setter(v):
            cb.blockSignals(True)
            cb.setChecked(bool(v))
            cb.blockSignals(False)

        def wire(cbk):
            cb.toggled.connect(lambda _on: cbk())

        return cb, getter, setter, wire

    if fname == "font_family":
        combo = QComboBox()
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        def getter():
            return combo.currentText()

        def setter(v):
            combo.blockSignals(True)
            idx = combo.findText(str(v))
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)

        def wire(cb):
            combo.currentTextChanged.connect(lambda _t: cb())

        return combo, getter, setter, wire

    if fname == "font_weight":
        # 字体族上下文由面板经 FieldEditor.set_family_context 注入；
        # 数据值是 int 字重，"(default)" 条目按旧 StyleDetail 语义映射 Normal。
        state = {"family": ""}
        combo = QComboBox()

        def _reload(family: str, keep: Optional[int]):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(QCoreApplication.translate("StyleFormatEditor", "(default)"), None)
            for style in shared.FONT_STYLES.get(family, []):
                combo.addItem(style, style)
            if keep is not None:
                for i in range(combo.count()):
                    raw = combo.itemData(i)
                    if raw is not None and QFontDatabase.weight(family, raw) == keep:
                        combo.setCurrentIndex(i)
                        break
            combo.blockSignals(False)

        def getter():
            raw = combo.currentData()
            if raw is None:
                return int(QFont.Weight.Normal)
            return QFontDatabase.weight(state["family"], raw)

        def setter(v):
            _reload(state["family"], None if v is None else int(v))

        def wire(cb):
            combo.currentIndexChanged.connect(lambda _i: cb())

        combo._reload_weights = _reload  # type: ignore[attr-defined]
        combo._weight_state = state  # type: ignore[attr-defined]
        return combo, getter, setter, wire

    if fname == "font_size":
        s = _spin(lo=1, hi=999, dec=1, step=0.5, suffix=" px")

        def getter():
            return s.value()

        def setter(v):
            s.blockSignals(True)
            s.setValue(float(v))
            s.blockSignals(False)

        def wire(cb):
            s.valueChanged.connect(lambda _v: cb())

        return s, getter, setter, wire

    if fname == "stroke_width":
        s = _spin(lo=0, hi=50, dec=1, step=0.1, suffix=" px")

        def getter():
            return s.value()

        def setter(v):
            s.blockSignals(True)
            s.setValue(float(v))
            s.blockSignals(False)

        def wire(cb):
            s.valueChanged.connect(lambda _v: cb())

        return s, getter, setter, wire

    if fname == "opacity":
        s = _spin(lo=0, hi=1, dec=2, step=0.05)

        def getter():
            return s.value()

        def setter(v):
            s.blockSignals(True)
            s.setValue(float(v))
            s.blockSignals(False)

        def wire(cb):
            s.valueChanged.connect(lambda _v: cb())

        return s, getter, setter, wire

    if fname == "line_spacing":
        s = _spin(lo=0.1, hi=10, dec=2, step=0.05)

        def getter():
            return s.value()

        def setter(v):
            s.blockSignals(True)
            s.setValue(float(v))
            s.blockSignals(False)

        def wire(cb):
            s.valueChanged.connect(lambda _v: cb())

        return s, getter, setter, wire

    if fname == "letter_spacing":
        s = _spin(lo=0, hi=100, dec=2, step=0.05)

        def getter():
            return s.value()

        def setter(v):
            s.blockSignals(True)
            s.setValue(float(v))
            s.blockSignals(False)

        def wire(cb):
            s.valueChanged.connect(lambda _v: cb())

        return s, getter, setter, wire

    raise ValueError(f"no editor for field: {fname!r}")


class FieldEditor(QWidget):
    """One field row: [label 100px] [control].

    ``value``/``set_value`` speak raw FontFormat value types; the
    ``value_changed`` signal carries the field name after any edit.
    只读展示（效果摘要）不走 FieldEditor，见 ``effects_tokens``。
    """

    value_changed = Signal(str)

    def __init__(self, fname: str, parent=None):
        super().__init__(parent)
        self.fname = fname
        self._control, self._getter, self._setter, self._wire = _build_control(fname)
        self.editable = True

        self._label = QLabel(field_label(fname))
        self._label.setFixedWidth(100)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setSpacing(6)
        lay.addWidget(self._label)
        lay.addWidget(self._control, 1)

        if self.editable:
            self._wire(lambda: self.value_changed.emit(self.fname))

    def value(self) -> Any:
        return self._getter()

    def set_value(self, v: Any):
        self._setter(v)

    def set_modified(self, modified: bool):
        """Highlight the row when its value differs from the baseline."""
        self.setProperty("modified", modified)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_family_context(self, family: str):
        """font_weight 组合框需随字体族重载（其余字段忽略）。"""
        if self.fname != "font_weight":
            return
        keep = None
        try:
            keep = self._getter() if isinstance(self._getter(), int) else None
        except Exception:
            keep = None
        self._control._weight_state["family"] = family  # type: ignore[attr-defined]
        self._control._reload_weights(family, keep)  # type: ignore[attr-defined]


class FormatGroupCard(QFrame):
    """Collapsible field group with a status badge in the header."""

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("FormatGroupCard")

        self._toggle = QToolButton()
        self._toggle.setObjectName("GroupToggle")
        self._toggle.setText(self.title())
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(Qt.ArrowType.DownArrow)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(True)
        self._toggle.setStyleSheet("QToolButton { border: none; background: transparent; font-weight: bold; }")
        self._toggle.setFixedWidth(110)
        self._toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._toggle.toggled.connect(self._on_toggled)

        self._status = QLabel()
        self._status.setObjectName("GroupStatus")

        header = QHBoxLayout()
        header.setContentsMargins(0, 2, 0, 2)
        header.setSpacing(6)
        header.addWidget(self._toggle)
        header.addWidget(self._status, 1)

        self._body = QWidget()
        self._body_lay = QVBoxLayout(self._body)
        self._body_lay.setContentsMargins(0, 0, 0, 2)
        self._body_lay.setSpacing(0)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(0)
        lay.addLayout(header)
        lay.addWidget(self._body)

    def title(self) -> str:
        return GROUP_TITLES.get(self.key, self.key)

    def set_fields(self, editors: List[FieldEditor]):
        """Replace the body rows with *editors* (reparented).

        Editors are persistent objects owned by the panel and reused across
        selections — detach old rows without destroying them.
        """
        while self._body_lay.count():
            item = self._body_lay.takeAt(0)
            wdg = item.widget()
            if wdg is not None:
                wdg.setParent(None)
                wdg.hide()
        for ed in editors:
            ed.setParent(self._body)
            ed.show()
            self._body_lay.addWidget(ed)
        self._update_visible()

    def set_summary_only(self, text: str):
        """只读摘要模式：无正文行，徽标显示 *text*，不可展开。

        效果组用——阴影/渐变等只笼统标记，不提供编辑。*text* 为空时整卡隐藏。
        """
        while self._body_lay.count():
            item = self._body_lay.takeAt(0)
            wdg = item.widget()
            if wdg is not None:
                wdg.setParent(None)
                wdg.hide()
        self._status.setText(text)
        self._status.setProperty("modified", False)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)
        if text:
            self.show()
            self.set_collapsed(True)
            self._toggle.setEnabled(False)
        else:
            self._toggle.setEnabled(True)
            self.hide()

    def set_collapsed(self, collapsed: bool):
        self._toggle.setChecked(not collapsed)

    def is_collapsed(self) -> bool:
        return not self._toggle.isChecked()

    def set_status(self, changed: int):
        if not self._body_lay.count():
            self._status.setText("")
            self.hide()
            return
        self.show()
        if changed <= 0:
            self._status.setText(
                QCoreApplication.translate("StyleFormatEditor", "Same as baseline")
            )
            self._status.setProperty("modified", False)
        else:
            self._status.setText(
                QCoreApplication.translate("StyleFormatEditor", "● {n} modified").format(n=changed)
            )
            self._status.setProperty("modified", True)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)

    def _on_toggled(self, checked: bool):
        self._toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )
        self._update_visible()

    def _update_visible(self):
        self._body.setVisible(self._toggle.isChecked())


class FormatEditorPanel(QScrollArea):
    """Four-group editor over a FontFormat baseline.

    Usage::

        panel.set_format(ffmt)                 # baseline = deepcopy(ffmt)
        panel.set_format(ffmt, baseline=base, only_fields={"font_size", ...})
        changed = panel.changed_values()       # quantized diff vs baseline
    """

    field_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FormatEditorPanel")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._baseline = None
        self._editors: Dict[str, FieldEditor] = {}
        self._cards: Dict[str, FormatGroupCard] = {}
        self._only_fields: Optional[set] = None

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        for key, fnames in FIELD_GROUPS.items():
            card = FormatGroupCard(key)
            self._cards[key] = card
            lay.addWidget(card)
            if key == "effects":
                # 效果组只读摘要：不建编辑器，set_format 时填摘要徽标
                continue
            for fname in fnames:
                ed = FieldEditor(fname)
                ed.value_changed.connect(self._on_field_changed)
                self._editors[fname] = ed
                if fname == "font_family":
                    ed.value_changed.connect(self._on_family_changed)
        lay.addStretch()
        self.setWidget(inner)

    # -- population ------------------------------------------------------

    def set_format(
        self,
        ffmt,
        baseline=None,
        only_fields: Optional[set] = None,
    ):
        """Sync editors to *ffmt*.

        *baseline* — the diff reference (default: deepcopy of *ffmt*, i.e.
        "nothing changed yet"). *only_fields* — variant mode: restrict the
        rendered fields; groups without any renderable field are hidden.
        """
        from utils.config import pcfg

        self._baseline = (baseline or ffmt).deepcopy()
        self._only_fields = set(only_fields) if only_fields is not None else None

        family = ffmt.font_family
        if "font_family" in self._editors:
            combo = self._editors["font_family"]._control
            combo.blockSignals(True)
            combo.clear()
            # 惰性兜底：面板可能早于 MainWindow 的字体枚举创建（离屏
            # 测试更是永不调用），空列表时下拉会只剩补插的当前字体一项
            if not shared.ALL_FONT_FAMILIES:
                shared.init_font_list()
            families = shared.get_filtered_font_list(pcfg.excluded_fonts)
            # 当前字体族必须可见可选：不在过滤列表（未安装/被排除/离屏
            # 空字体库）时补插首项，否则下拉会静默回落到别的字体。
            if family and family not in families:
                families = [family] + list(families)
            if families:
                combo.addItems(families)
            idx = combo.findText(family)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.blockSignals(False)

        changed_fields = set()
        for fname, ed in self._editors.items():
            if self._only_fields is not None and fname not in self._only_fields:
                continue
            value = getattr(ffmt, fname, None)
            ed.set_family_context(family)
            if value is not None:
                ed.set_value(value)
            else:
                # None 字段（如 font_weight=None）按"默认"处理
                ed.set_value(self._default_for(fname))
            try:
                if self._differs(fname, ed.value()):
                    changed_fields.add(fname)
            except (TypeError, ValueError):
                pass

        for key, card in self._cards.items():
            if key == "effects":
                card.set_summary_only(" · ".join(effects_tokens(ffmt, self._only_fields)))
                continue
            fns = [
                self._editors[f]
                for f in FIELD_GROUPS[key]
                if f in self._editors
                and (self._only_fields is None or f in self._only_fields)
            ]
            card.set_fields(fns)
            n_changed = sum(
                1
                for f in FIELD_GROUPS[key]
                if f in changed_fields
                and (self._only_fields is None or f in self._only_fields)
            )
            card.set_status(n_changed)
            if fns:
                # 差异优先：有改动的组自动展开，其余折叠
                card.set_collapsed(n_changed == 0)
        for fname, ed in self._editors.items():
            ed.set_modified(fname in changed_fields)

    def _default_for(self, fname: str) -> Any:
        if fname == "font_weight":
            return int(QFont.Weight.Normal)
        if fname in _ENUM_CHOICES:
            return _ENUM_CHOICES[fname][0][1]
        if fname in ("italic", "underline", "strikeout", "vertical",
                     "standard_vertical_roman_alignment"):
            return False
        return 0.0

    # -- collection --------------------------------------------------------

    def _differs(self, fname: str, v: Any) -> bool:
        """Diff one editable value against the baseline.

        基线为 None（如未设字重）且编辑值是中性默认时视为一致——否则
        大样式一打开就误报"已修改"，且 patch 会把默认值压进块级 override。
        """
        old = getattr(self._baseline, fname, None)
        if old is None:
            return v != self._default_for(fname)
        return quantize_field(fname, v) != quantize_field(fname, old)

    def changed_values(self) -> Dict[str, Any]:
        """Quantized diff of editable fields against the baseline.

        与旧 ``StyleDetail._collect_changed`` 语义一致（量化比较），
        未变化字段不得进入（避免压掉块级 override）。
        """
        changed: Dict[str, Any] = {}
        if self._baseline is None:
            return changed
        for fname, ed in self._editors.items():
            if not ed.editable:
                continue
            if self._only_fields is not None and fname not in self._only_fields:
                continue
            v = ed.value()
            if self._differs(fname, v):
                changed[fname] = v
        return changed

    def current_values(self, only_fields: Optional[set] = None) -> Dict[str, Any]:
        """All editable field values (for full-parameter apply paths).

        *only_fields* defaults to the panel's current restriction (variant
        mode: the override fields only; full mode: everything editable).
        """
        if only_fields is None:
            only_fields = self._only_fields
        out: Dict[str, Any] = {}
        for fname, ed in self._editors.items():
            if not ed.editable:
                continue
            if only_fields is not None and fname not in only_fields:
                continue
            out[fname] = ed.value()
        return out

    def field_value(self, fname: str) -> Any:
        return self._editors[fname].value()

    def set_field_value(self, fname: str, v: Any):
        self._editors[fname].set_value(v)

    def baseline_copy(self):
        """Deepcopy of the baseline FontFormat (None before set_format)."""
        return self._baseline.deepcopy() if self._baseline is not None else None

    def sync_into(self, ffmt):
        """Write every visible editable editor value into *ffmt*."""
        for fname, ed in self._editors.items():
            if not ed.editable:
                continue
            if self._only_fields is not None and fname not in self._only_fields:
                continue
            try:
                setattr(ffmt, fname, copy_value(ed.value()))
            except (TypeError, ValueError):
                pass

    def group_of(self, fname: str) -> Optional[str]:
        for key, fns in FIELD_GROUPS.items():
            if fname in fns:
                return key
        return None

    def scroll_to_group(self, key: str):
        card = self._cards.get(key)
        if card is None:
            return
        if key == "effects":
            # 只读摘要卡不可展开，滚动可见即可
            self.ensureWidgetVisible(card)
            return
        card.set_collapsed(False)
        self.ensureWidgetVisible(card)

    # -- internals -----------------------------------------------------------

    def _on_field_changed(self, fname: str):
        self._refresh_group_status()
        self.field_changed.emit(fname)

    def _on_family_changed(self, _fname: str):
        family = self.field_value("font_family")
        w = self._editors.get("font_weight")
        if w is not None:
            w.set_family_context(family)

    def _refresh_group_status(self):
        changed = self.changed_values()
        for key, card in self._cards.items():
            if key == "effects":
                continue
            n = sum(
                1
                for f in FIELD_GROUPS[key]
                if f in changed
                and (self._only_fields is None or f in self._only_fields)
            )
            card.set_status(n)
