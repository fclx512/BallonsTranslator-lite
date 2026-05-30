from typing import Callable

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout

from utils import config as C
from utils.fontformat import FontFormat

from .custom_widget import (
    PanelArea,
    SmallComboBox,
    SmallParamLabel,
    SmallSizeComboBox,
    SmallSizeControlLabel,
)


class TextAdvancedFormatPanel(PanelArea):
    param_changed = Signal(str, object)

    def __init__(
        self,
        panel_name: str,
        config_name: str,
        config_expand_name: str,
        on_format_changed: Callable,
    ):
        super().__init__(panel_name, config_name, config_expand_name)

        self.active_format: FontFormat = None
        self.on_format_changed = on_format_changed

        self.punct_align_combobox = SmallComboBox(
            parent=self, options=[self.tr("Center"), self.tr("Upper-Right")]
        )
        self.punct_align_combobox.activated.connect(self.on_punct_align_changed)
        punct_align_label = SmallParamLabel(self.tr("Punctuation Alignment"))
        punct_align_layout = QHBoxLayout()
        punct_align_layout.addWidget(punct_align_label)
        punct_align_layout.addWidget(self.punct_align_combobox)

        self.opacity_box = SmallSizeComboBox([0, 1], "opacity", self, init_value=1.0)
        self.opacity_box.addItems([str(v) for v in C.pcfg.opacity_presets])
        self.opacity_box.setToolTip(self.tr("Set Text Opacity"))
        self.opacity_box.param_changed.connect(self.on_format_changed)
        self.opacity_label = SmallSizeControlLabel(
            self,
            direction=1,
            text=self.tr("Opacity"),
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        self.opacity_label.size_ctrl_changed.connect(self.opacity_box.changeByDelta)
        self.opacity_label.btn_released.connect(
            lambda: self.on_format_changed("opacity", self.opacity_box.value())
        )
        opacity_layout = QHBoxLayout()
        opacity_layout.addWidget(self.opacity_label)
        opacity_layout.addWidget(self.opacity_box)

        # shadow / gradient trigger buttons (replaces old inline groups)
        btn_style = (
            "QPushButton { border: 1px solid palette(mid); border-radius: 4px; "
            "padding: 2px 8px; font-size: 11px; } "
            "QPushButton:hover { border-color: palette(highlight); }"
        )
        self.shadow_btn = QPushButton(self.tr("Shadow"))
        self.shadow_btn.setStyleSheet(btn_style)
        self.shadow_btn.setToolTip(self.tr("Edit shadow settings"))
        self.shadow_btn.setObjectName("inpanel_effect_btn")

        self.gradient_btn = QPushButton(self.tr("Gradient"))
        self.gradient_btn.setStyleSheet(btn_style)
        self.gradient_btn.setToolTip(self.tr("Edit gradient settings"))
        self.gradient_btn.setObjectName("inpanel_effect_btn")

        btns_layout = QHBoxLayout()
        btns_layout.addWidget(self.shadow_btn)
        btns_layout.addWidget(self.gradient_btn)
        btns_layout.addStretch()

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.scrollContent.after_resized.connect(self.adjuset_size)

        hlayout = QHBoxLayout()
        hlayout.setSpacing(8)
        hlayout.addLayout(opacity_layout)
        hlayout.addLayout(punct_align_layout)
        hlayout.addStretch()

        self.linespacing_type_combobox = SmallComboBox(
            parent=self, options=[self.tr("Proportional"), self.tr("Distance")]
        )
        self.linespacing_type_combobox.activated.connect(
            self.on_linespacing_type_changed
        )
        linespacing_type_label = SmallParamLabel(self.tr("Line Spacing Type"))
        linespacing_layout = QHBoxLayout()
        linespacing_layout.addWidget(linespacing_type_label)
        linespacing_layout.addWidget(self.linespacing_type_combobox)
        linespacing_layout.addStretch()

        vlayout = QVBoxLayout()
        vlayout.addLayout(hlayout)
        vlayout.addLayout(linespacing_layout)
        vlayout.addLayout(btns_layout)
        vlayout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.setContentLayout(vlayout)
        self.vlayout = vlayout

    def adjuset_size(self):
        TEXT_ADVANCED_PANEL_MAXH = 300
        self.setFixedHeight(min(TEXT_ADVANCED_PANEL_MAXH, self.scrollContent.height()))

    def on_punct_align_changed(self):
        self.on_format_changed(
            "punctuation_alignment", self.punct_align_combobox.currentIndex()
        )

    def on_linespacing_type_changed(self):
        self.on_format_changed(
            "line_spacing_type", self.linespacing_type_combobox.currentIndex()
        )

    def set_active_format(self, font_format: FontFormat):
        self.active_format = font_format
        self.punct_align_combobox.setCurrentIndex(font_format.punctuation_alignment)
        self.linespacing_type_combobox.setCurrentIndex(font_format.line_spacing_type)
        self.opacity_box.setValue(font_format.opacity)
        self._update_effect_btns(font_format)

    def reload_presets(self):
        cur = self.opacity_box.value()
        self.opacity_box.blockSignals(True)
        self.opacity_box.clear()
        self.opacity_box.addItems([str(v) for v in C.pcfg.opacity_presets])
        self.opacity_box.setValue(cur)
        self.opacity_box.blockSignals(False)

    def _update_effect_btns(self, font_format: FontFormat):
        base = (
            "QPushButton { border: 1px solid palette(mid); border-radius: 4px; "
            "padding: 2px 8px; font-size: 11px; } "
            "QPushButton:hover { border-color: palette(highlight); }"
        )
        has_shadow = font_format.shadow_radius > 0 and font_format.shadow_strength > 0
        if has_shadow:
            sc = font_format.shadow_color
            r, g, b = int(sc[0]), int(sc[1]), int(sc[2])
            self.shadow_btn.setStyleSheet(
                base
                + f" QPushButton {{ background: rgba({r},{g},{b},40); border-color: rgb({r},{g},{b}); }}"
            )
        else:
            self.shadow_btn.setStyleSheet(base)

        has_grad = font_format.gradient_enabled
        if has_grad:
            gc = font_format.gradient_start_color
            r, g, b = int(gc[0]), int(gc[1]), int(gc[2])
            ec = font_format.gradient_end_color
            er, eg, eb = int(ec[0]), int(ec[1]), int(ec[2])
            self.gradient_btn.setStyleSheet(
                base
                + f" QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                f"stop:0 rgba({r},{g},{b},60), stop:1 rgba({er},{eg},{eb},60)); }}"
            )
        else:
            self.gradient_btn.setStyleSheet(base)
