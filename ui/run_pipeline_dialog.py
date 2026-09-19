"""Run dialog — enable the pipeline stages and set their run-time options.

Split out of ``ui/mainwindow.py`` and reworked after upstream's
``ballontranslator/ui/run_pipeline_dialog.py``:

* the four stages are an "Activate Modules" grid — one icon toggle plus one
  module selector each, in pipeline order (detect → OCR → inpaint → translate);
* every stage owns a collapsible options section holding the run-time switches
  that used to live in the settings panel.

Deliberate deviations from upstream, each because the fork lacks a piece or the
upstream behaviour would be worse here:

* **native window chrome** — upstream's frameless shell and
  ``DialogCloseButton`` are not ported; the dialog keeps a normal title bar.
* **no per-module config gear** — the run dialog is modal, so it cannot raise
  the settings overlay above itself.  The bottom bar's per-stage gears already
  open the matching settings tab.  The grid therefore matches the reference
  design: icon plus selector only.
* **fork page-range row** — ``PageRangeProgressWidget`` (fork port of upstream's
  ``ui/page_range_progress.py``): start/end page boxes plus a completion track.
  Upstream's widget is now the reference implementation, so no *All Pages*
  toggle is needed — the full range *is* all pages.
* **themed stage accents** — ``@accentDetect`` / ``@accentOCR`` /
  ``@accentInpaint`` / ``@accentTranslate`` from ``config/themes.json`` instead
  of upstream's hardcoded modality palette, so the stage icons match the LLM
  profile badges.

The dialog owns no manager state: it reads ``pcfg`` directly and reports
toggles, module picks and language picks back through signals.
"""

import os.path as osp

from qtpy.QtCore import QEvent, QSize, Qt, Signal
from qtpy.QtGui import QIcon
from qtpy.QtWidgets import (
    QAbstractButton,
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from modules import (
    GET_VALID_INPAINTERS,
    GET_VALID_OCR,
    GET_VALID_TEXTDETECTORS,
    GET_VALID_TRANSLATORS,
    HIDDEN_INPAINTERS,
)
from utils.config import SingleBlkTranslateMode, pcfg

from .custom_widget import (
    ConfigCheckBox,
    ConfigComboBox,
    ExpandingToolButton,
    NoArrowsSpinBox,
    PageRangeProgressWidget,
)
from .icon_rendering import render_svg_pixmap
from .misc import (
    get_theme_color,
    mark_module_selector_status,
    themed_icon_path,
)

#: Pipeline stage indices, matching ``ModuleConfig.stage_enabled``.
STAGE_DETECT = 0
STAGE_OCR = 1
STAGE_TRANSLATE = 2
STAGE_INPAINT = 3

#: Inpainters hidden from the run-time selector (single source of truth in
#: ``modules/__init__.py::HIDDEN_INPAINTERS``; the bottom bar hides them too).

SETTINGS_BODY_INDENT = 18
MODULE_SELECTOR_WIDTH = 150


class PipelineModuleButton(QAbstractButton):
    """Checkable stage icon: colored badge when on, muted glyph when off."""

    def __init__(
        self,
        text: str,
        accent_key: str,
        active_icon: str,
        inactive_icon: str,
        parent: QWidget = None,
    ) -> None:
        super().__init__(parent)
        self._accent_key = accent_key
        self._active_icon = active_icon
        self._inactive_icon = inactive_icon
        self.setObjectName("RunPipelineModuleButton")
        self.setCheckable(True)
        self.setChecked(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text)
        self.setToolTip(text)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(0)
        self.icon_label = QLabel(self)
        self.icon_label.setObjectName("RunPipelineModuleIcon")
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.icon_label)

        self.toggled.connect(self._refresh_visuals)
        self._refresh_visuals(self.isChecked())

    def _refresh_visuals(self, active: bool) -> None:
        accent = get_theme_color(key=self._accent_key)
        if active:
            kwargs = {
                "override_fill": accent.name(),
                "inset": 2,
                "background_rgba": get_theme_color(
                    alpha=45, key=self._accent_key
                ).getRgb(),
                "background_radius": 5,
            }
        else:
            kwargs = {
                "override_fill": get_theme_color(
                    key="@disabledForegroundColor"
                ).name()
            }
        self.icon_label.setPixmap(
            render_svg_pixmap(
                themed_icon_path(self._active_icon if active else self._inactive_icon),
                self.icon_label.width(),
                self.icon_label.height(),
                self.icon_label.devicePixelRatioF(),
                **kwargs,
            )
        )

    def paintEvent(self, event) -> None:
        option = QStyleOptionButton()
        option.initFrom(self)
        if self.isDown():
            option.state |= QStyle.StateFlag.State_Sunken
        if self.isChecked():
            option.state |= QStyle.StateFlag.State_On
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)

    def changeEvent(self, event) -> None:
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.PaletteChange):
            self._refresh_visuals(self.isChecked())
        return super().changeEvent(event)


class PipelineModuleActivator(QWidget):
    """One pipeline stage: icon toggle plus its always-visible module selector.

    Clicking anywhere on an inactive row turns the stage back on, so a
    deactivated stage can be revived without hunting for the small icon.
    """

    module_selected = Signal(str, str)

    def __init__(
        self,
        module_type: str,
        module_name: str,
        options,
        text: str,
        accent_key: str,
        active_icon: str,
        inactive_icon: str,
        parent: QWidget = None,
    ) -> None:
        super().__init__(parent)
        self.module_type = module_type
        self.setObjectName("RunPipelineModuleActivator")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.button = PipelineModuleButton(
            text, accent_key, active_icon, inactive_icon, self
        )
        layout.addWidget(self.button)

        self.selector = ConfigComboBox(
            fix_size=False, options=list(options)
        )
        self.selector.setObjectName("RunPipelineModuleSelector")
        self.selector.setFixedWidth(MODULE_SELECTOR_WIDTH)
        self.selector.setCurrentText(module_name)
        self.selector.setToolTip(module_name)
        mark_module_selector_status(self.selector, module_type)
        self.selector.currentTextChanged.connect(self._on_module_selected)
        layout.addWidget(self.selector)
        layout.addStretch(1)

        self.button.toggled.connect(self._refresh_active_state)
        self._refresh_active_state(self.button.isChecked())

    def _refresh_active_state(self, active: bool) -> None:
        for widget in (self, self.selector):
            widget.setProperty("moduleActive", active)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    def mousePressEvent(self, event) -> None:
        if (
            not self.button.isChecked()
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self.button.setChecked(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def _on_module_selected(self, module_name: str) -> None:
        self.selector.setToolTip(module_name)
        self.module_selected.emit(self.module_type, module_name)

    def set_module(self, module_name: str) -> None:
        if self.selector.currentText() == module_name:
            return
        self.selector.blockSignals(True)
        self.selector.setCurrentText(module_name)
        self.selector.blockSignals(False)
        self.selector.setToolTip(module_name)


class RunPipelineDialog(QDialog):
    """Choose the pipeline stages to run and set their run-time options."""

    stage_toggled = Signal(int, bool)
    module_selected = Signal(str, str)
    translate_source_changed = Signal(str)
    translate_target_changed = Signal(str)

    #: Collapsed/expanded memory, kept for the process lifetime so reopening
    #: the dialog does not fold everything back up.
    _sections_expanded = {}

    #: Last page range ``(start, end)`` of the session, 1-based inclusive.
    _page_range = (1, None)

    def __init__(
        self,
        parent: QWidget = None,
        *,
        page_names=None,
        finished_pages=None,
        translator=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Run"))
        self.setSizeGripEnabled(False)

        self._page_names = list(page_names or [])
        self._finished_pages = list(finished_pages or [])
        self._stage_activators = {}
        self._stage_sections = {}
        self._stage_headers = {}
        self._stage_bodies = {}

        root = QVBoxLayout(self)
        root.setSpacing(10)

        self.tab_bar = QTabBar()
        self.tab_bar.addTab(self.tr("Pipeline"))
        self.tab_bar.addTab(self.tr("Render Only"))
        self.tab_bar.setExpanding(False)
        self.tab_bar.setDrawBase(True)
        root.addWidget(self.tab_bar)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_pipeline_page())
        self.stack.addWidget(self._build_render_page())
        self.tab_bar.currentChanged.connect(self.stack.setCurrentIndex)
        root.addWidget(self.stack)

        root.addLayout(self._build_button_row())

        # The glossary path is committed straight to ``pcfg`` while browsing,
        # so it survives a Cancel — matching the old dialog's behaviour.
        self._refresh_stage_sections()
        self._resize_to_fit()

    # ── Pipeline page ────────────────────────────────────────────────

    def _build_pipeline_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("RunPipelinePipelinePage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        layout.addWidget(self._section_divider(self.tr("Activate Modules")))
        layout.addWidget(self._build_stage_grid())

        self.range_frame = self._build_page_range()
        layout.addWidget(self.range_frame)

        for builder in (
            self._build_detect_options,
            self._build_ocr_options,
            self._build_inpaint_options,
            self._build_translate_options,
        ):
            builder(layout)

        self.wo_update_cb = ConfigCheckBox(self.tr("Run without update textstyle"))
        layout.addWidget(self.wo_update_cb)
        layout.addStretch(1)
        return page

    def _build_stage_grid(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("RunPipelineStages")
        grid = QGridLayout(frame)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)

        # Display order follows the pipeline; ``stage`` is the config index.
        specs = (
            (
                STAGE_DETECT, "textdetector", self.tr("Text Detection"),
                "@accentDetect", "textdetect_activate.svg", "textdetect.svg",
            ),
            (
                STAGE_OCR, "ocr", self.tr("OCR"),
                "@accentOCR", "eye.svg", "eye_disabled.svg",
            ),
            (
                STAGE_INPAINT, "inpainter", self.tr("Inpainting"),
                "@accentInpaint", "image.svg", "image_disabled.svg",
            ),
            (
                STAGE_TRANSLATE, "translator", self.tr("Translation"),
                "@accentTranslate", "text.svg", "text_disabled.svg",
            ),
        )
        for display_index, (
            stage, module_type, label, accent_key, active_icon, inactive_icon,
        ) in enumerate(specs):
            module_name = pcfg.module[module_type]
            options = self._module_options(module_type)
            if module_name and module_name not in options:
                # Keep the current engine selectable even when it is filtered
                # out of the list (the online inpainter, for instance).
                options = list(options) + [module_name]
            activator = PipelineModuleActivator(
                module_type,
                module_name,
                options,
                label,
                accent_key,
                active_icon,
                inactive_icon,
                frame,
            )
            activator.button.setChecked(bool(pcfg.module.stage_enabled(stage)))
            activator.button.toggled.connect(
                lambda checked, s=stage: self._on_stage_toggled(s, checked)
            )
            activator.module_selected.connect(self.module_selected.emit)
            grid.addWidget(activator, display_index // 2, display_index % 2)
            self._stage_activators[stage] = activator

        return frame

    @staticmethod
    def _module_options(module_type: str):
        if module_type == "textdetector":
            return GET_VALID_TEXTDETECTORS()
        if module_type == "ocr":
            return GET_VALID_OCR()
        if module_type == "inpainter":
            return [m for m in GET_VALID_INPAINTERS() if m not in HIDDEN_INPAINTERS]
        return GET_VALID_TRANSLATORS()

    def _build_page_range(self) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(frame)
        start, end = type(self)._page_range
        self.page_range = PageRangeProgressWidget(
            self._page_names,
            start=start,
            end=end,
            parent=frame,
        )
        self.page_range.set_finished_pages(self._finished_pages)
        self.page_range.range_changed.connect(self._on_page_range_changed)
        layout.addWidget(self.page_range)
        return frame

    def _on_page_range_changed(self, start: int, end: int) -> None:
        # Remembered for the process lifetime, like the section folds above.
        type(self)._page_range = (start, end)

    # ── Collapsible stage sections ───────────────────────────────────

    def _section_divider(self, title: str) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(title)
        label.setObjectName("RunPipelineSectionTitle")
        layout.addWidget(label)
        line = QFrame()
        line.setObjectName("RunPipelineSectionLine")
        line.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(line)
        return holder

    def _add_settings_section(self, layout: QVBoxLayout, stage: int, title: str):
        section = QWidget()
        section.setObjectName("RunPipelineSettingsSection")
        section.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(6)

        expanded = type(self)._sections_expanded.get(stage, False)
        header = ExpandingToolButton(section)
        header.setObjectName("RunPipelineModuleSettingsHeader")
        header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        header.setText("\u2009" + title)
        header.setCheckable(True)
        header.setChecked(expanded)
        header.setIconSize(QSize(12, 12))
        header.setIcon(QIcon(self._chevron_pixmap(expanded)))
        section_layout.addWidget(header)

        body = QWidget(section)
        body.setObjectName("RunPipelineModuleSettingsBody")
        body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(SETTINGS_BODY_INDENT, 0, 0, 0)
        body_layout.setSpacing(8)
        section_layout.addWidget(body)
        body.setVisible(expanded)

        header.toggled.connect(
            lambda checked, s=stage: self._set_section_expanded(s, checked)
        )
        self._stage_sections[stage] = section
        self._stage_headers[stage] = header
        self._stage_bodies[stage] = body
        layout.addWidget(section)
        return body_layout

    @staticmethod
    def _chevron_pixmap(expanded: bool):
        name = "chevron-down.svg" if expanded else "chevron-right.svg"
        return render_svg_pixmap(themed_icon_path(name), 12, 12, 1.0)

    def _set_section_expanded(self, stage: int, expanded: bool) -> None:
        type(self)._sections_expanded[stage] = expanded
        header = self._stage_headers.get(stage)
        if header is not None:
            header.setIcon(QIcon(self._chevron_pixmap(expanded)))
        body = self._stage_bodies.get(stage)
        if body is not None:
            body.setVisible(expanded)
        self._resize_to_fit()

    def _refresh_stage_sections(self) -> None:
        for stage, section in self._stage_sections.items():
            section.setVisible(self._stage_activators[stage].button.isChecked())
        self._resize_to_fit()

    def _on_stage_toggled(self, stage: int, checked: bool) -> None:
        self.stage_toggled.emit(stage, checked)
        self._refresh_stage_sections()
        self.run_btn.setEnabled(not pcfg.module.all_stages_disabled())

    # ── Stage options ────────────────────────────────────────────────

    def _add_checkbox(self, layout: QVBoxLayout, text: str, checked: bool, on_toggle):
        box = ConfigCheckBox(text)
        box.setChecked(bool(checked))
        box.toggled.connect(on_toggle)
        layout.addWidget(box)
        return box

    def _build_detect_options(self, layout: QVBoxLayout) -> None:
        body = self._add_settings_section(layout, STAGE_DETECT, self.tr("Text Detection"))

        def on_toggle(checked: bool):
            pcfg.module.keep_exist_textlines = checked

        self.keep_lines_cb = self._add_checkbox(
            body,
            self.tr("Keep Existing Lines"),
            pcfg.module.keep_exist_textlines,
            on_toggle,
        )

    def _build_ocr_options(self, layout: QVBoxLayout) -> None:
        body = self._add_settings_section(layout, STAGE_OCR, self.tr("OCR"))
        hint = QLabel(self.tr("No run-time options for this stage."))
        hint.setObjectName("RunPipelineSettingLabel")
        body.addWidget(hint)

    def _build_inpaint_options(self, layout: QVBoxLayout) -> None:
        body = self._add_settings_section(
            layout, STAGE_INPAINT, self.tr("Inpainting")
        )

        def on_toggle(checked: bool):
            pcfg.module.check_need_inpaint = checked
            from modules.inpaint.base import InpainterBase

            InpainterBase.check_need_inpaint = checked

        self.skip_simple_cb = self._add_checkbox(
            body,
            self.tr("Skip simple cases"),
            pcfg.module.check_need_inpaint,
            on_toggle,
        )

    def _build_translate_options(self, layout: QVBoxLayout) -> None:
        body = self._add_settings_section(
            layout, STAGE_TRANSLATE, self.tr("Translation")
        )

        lang_row = QWidget()
        lang_layout = QHBoxLayout(lang_row)
        lang_layout.setContentsMargins(0, 0, 0, 0)
        lang_layout.setSpacing(6)
        lang_layout.addWidget(self._setting_label(self.tr("Source")))
        self.source_combobox = ConfigComboBox(fix_size=False)
        self.source_combobox.setFixedWidth(MODULE_SELECTOR_WIDTH)
        self.source_combobox.currentTextChanged.connect(
            self.translate_source_changed.emit
        )
        lang_layout.addWidget(self.source_combobox)
        lang_layout.addSpacing(12)
        lang_layout.addWidget(self._setting_label(self.tr("Target")))
        self.target_combobox = ConfigComboBox(fix_size=False)
        self.target_combobox.setFixedWidth(MODULE_SELECTOR_WIDTH)
        self.target_combobox.currentTextChanged.connect(
            self.translate_target_changed.emit
        )
        lang_layout.addWidget(self.target_combobox)
        lang_layout.addStretch(1)
        body.addWidget(lang_row)

        mode_row = QWidget()
        mode_layout = QHBoxLayout(mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(6)
        mode_layout.addWidget(self._setting_label(self.tr("Single-Block Translation")))
        self.single_blk_combo = ConfigComboBox()
        self.single_blk_combo.setFixedWidth(MODULE_SELECTOR_WIDTH)
        self.single_blk_combo.addItem(self.tr("plain"), SingleBlkTranslateMode.Plain)
        self.single_blk_combo.addItem(self.tr("context"), SingleBlkTranslateMode.Context)
        index = self.single_blk_combo.findData(pcfg.module.single_blk_translate_mode)
        self.single_blk_combo.setCurrentIndex(max(index, 0))
        self.single_blk_combo.currentIndexChanged.connect(
            lambda: setattr(
                pcfg.module,
                "single_blk_translate_mode",
                self.single_blk_combo.currentData(),
            )
        )
        mode_layout.addWidget(self.single_blk_combo)
        mode_layout.addStretch(1)
        body.addWidget(mode_row)

        self._build_llm_context(body)

    def _setting_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("RunPipelineSettingLabel")
        return label

    def _build_llm_context(self, layout: QVBoxLayout) -> None:
        """LLM context injection + glossary, as in the old inline dialog."""
        header = QLabel(self.tr("Context"))
        header.setObjectName("RunPipelineSubsectionHeader")
        layout.addWidget(header)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        self.history_cb = ConfigCheckBox(self.tr("Inject Prior-Page History"))
        self.history_cb.setChecked(bool(pcfg.module.llm_translate_context))
        self.history_cb.toggled.connect(
            lambda checked: setattr(pcfg.module, "llm_translate_context", checked)
        )
        row_layout.addWidget(self.history_cb)
        self.story_cb = ConfigCheckBox(self.tr("Inject Story Context"))
        self.story_cb.setChecked(bool(pcfg.module.llm_story_context))
        self.story_cb.toggled.connect(
            lambda checked: setattr(pcfg.module, "llm_story_context", checked)
        )
        row_layout.addWidget(self.story_cb)
        row_layout.addStretch(1)

        self.token_label = self._setting_label(self.tr("Token Budget"))
        row_layout.addWidget(self.token_label)
        self.token_spin = NoArrowsSpinBox()
        self.token_spin.setRange(512, 16384)
        self.token_spin.setSingleStep(512)
        self.token_spin.setValue(pcfg.module.llm_prior_context_token_budget)
        self.token_spin.valueChanged.connect(
            lambda value: setattr(
                pcfg.module, "llm_prior_context_token_budget", value
            )
        )
        self.token_spin.setFixedWidth(80)
        row_layout.addWidget(self.token_spin)
        layout.addWidget(row)

        self.glossary_cb = ConfigCheckBox(
            self.tr("Enforce Term Consistency (Glossary)")
        )
        self.glossary_cb.setChecked(bool(pcfg.module.llm_glossary_path))
        layout.addWidget(self.glossary_cb)

        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(6)
        path_layout.addWidget(self._setting_label(self.tr("Glossary")))
        self.glossary_status = QLabel()
        path_layout.addWidget(self.glossary_status)
        browse_btn = QPushButton(self.tr("Browse..."))
        browse_btn.setFixedWidth(110)
        browse_btn.setFixedHeight(27)
        browse_btn.clicked.connect(self._browse_glossary)
        path_layout.addWidget(browse_btn)
        path_layout.addStretch(1)
        layout.addWidget(path_row)

        mode_row = QWidget()
        mode_layout = QHBoxLayout(mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(6)
        mode_layout.addWidget(self._setting_label(self.tr("Mode")))
        mode_layout.addStretch(1)
        self.glossary_mode_combo = ConfigComboBox()
        self.glossary_mode_combo.addItem(self.tr("Matching"), "matching")
        self.glossary_mode_combo.addItem(self.tr("All"), "all")
        index = self.glossary_mode_combo.findData(pcfg.module.llm_glossary_mode)
        if index >= 0:
            self.glossary_mode_combo.setCurrentIndex(index)
        self.glossary_mode_combo.currentIndexChanged.connect(
            lambda: setattr(
                pcfg.module,
                "llm_glossary_mode",
                self.glossary_mode_combo.currentData(),
            )
        )
        self.glossary_mode_combo.setFixedWidth(140)
        mode_layout.addWidget(self.glossary_mode_combo)
        layout.addWidget(mode_row)

        self._refresh_glossary()
        self.glossary_cb.toggled.connect(self._refresh_glossary)
        self._refresh_token_visibility(self.history_cb.isChecked())
        self.history_cb.toggled.connect(self._refresh_token_visibility)

    def _refresh_glossary(self, *_args) -> None:
        enabled = self.glossary_cb.isChecked()
        for widget in (
            self.glossary_status.parentWidget(),
            self.glossary_mode_combo.parentWidget(),
        ):
            widget.setVisible(enabled)
        path = pcfg.module.llm_glossary_path
        if path:
            self.glossary_status.setText("\u2713 " + osp.basename(path))
            self.glossary_status.setStyleSheet("color: #4caf50;")
        else:
            self.glossary_status.setText("\u25cb")
            self.glossary_status.setStyleSheet("color: #888;")
        self._resize_to_fit()

    def _refresh_token_visibility(self, enabled: bool) -> None:
        self.token_label.setVisible(enabled)
        self.token_spin.setVisible(enabled)
        self._resize_to_fit()

    def _browse_glossary(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Select Glossary File"),
            pcfg.module.llm_glossary_path or "",
            self.tr("Glossary files (*.json *.txt *.tsv);;All files (*)"),
        )
        if path:
            pcfg.module.llm_glossary_path = path
            self.glossary_cb.setChecked(True)
            self._refresh_glossary()

    # ── Render-only page ─────────────────────────────────────────────

    def _build_render_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("RunPipelineRenderingPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(
            self.tr("Render all result images from current project data.\nNo pipeline stages will be executed.")
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addStretch(1)
        return page

    def _build_button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)
        self.run_btn = QPushButton(self.tr("Run"))
        self.run_btn.setFixedWidth(90)
        self.run_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.setFixedWidth(90)
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(self.run_btn)
        row.addWidget(cancel_btn)
        self.run_btn.setEnabled(not pcfg.module.all_stages_disabled())
        return row

    # ── Public API ───────────────────────────────────────────────────

    def set_module_selection(self, module_type: str, module_name: str) -> None:
        """Mirror an externally changed module into the matching selector."""
        for activator in self._stage_activators.values():
            if activator.module_type == module_type:
                activator.set_module(module_name)
                return

    def set_translator_metadata(
        self, lang_source, lang_target, supported_src_list, supported_tgt_list
    ) -> None:
        """Populate the language selectors once a translator has loaded."""
        for combo, values, current in (
            (self.source_combobox, supported_src_list, lang_source),
            (self.target_combobox, supported_tgt_list, lang_target),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(list(values or []))
            combo.setCurrentText(current)
            combo.blockSignals(False)

    def is_render_only(self) -> bool:
        return self.tab_bar.currentIndex() == 1

    def run_without_textstyle_update(self) -> bool:
        return self.wo_update_cb.isChecked()

    def page_filter(self):
        """Page names in the selected range, or None when it spans everything."""
        total = len(self._page_names)
        if not total:
            return None
        start, end = self.page_range.range_values()
        if start <= 1 and end >= total:
            return None
        return self._page_names[start - 1:end]

    # ── Layout ───────────────────────────────────────────────────────

    def _resize_to_fit(self) -> None:
        """Recompute the fixed size after a section folds or unfolds."""
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.adjustSize()
        screen = QApplication.primaryScreen()
        max_height = self.height()
        if screen is not None:
            max_height = min(
                max_height, int(screen.availableGeometry().height() * 0.9)
            )
        self.setFixedSize(self.width(), max_height)
