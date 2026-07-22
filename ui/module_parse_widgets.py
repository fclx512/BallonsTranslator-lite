from typing import Callable

from qtpy.QtCore import Qt, Signal
from qtpy.QtGui import QDoubleValidator
from qtpy.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from modules import (
    DEFAULT_DEVICE,
    GET_VALID_INPAINTERS,
    GET_VALID_OCR,
    GET_VALID_TEXTDETECTORS,
    GET_VALID_TRANSLATORS,
    GPUINTENSIVE_SET,
    BaseTranslator,
)
from utils.logger import logger as LOGGER
from utils.shared import (
    CONFIG_COMBOBOX_HEIGHT,
    CONFIG_COMBOBOX_LONG,
    size2width,
)

from .custom_widget import (
    ConfigComboBox,
    ConfigLineEdit,
    ConfigSectionHeader,
    ConfigTextEdit,
    ParamComboBox,
    ParamNameLabel,
)


class ParamCheckGroup(QWidget):
    paramwidget_edited = Signal(str, dict)

    def __init__(self, param_key, check_group: dict, parent=None) -> None:
        super().__init__(parent=parent)
        self.param_key = param_key
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label2widget = {}
        ncols = 3
        for ii, (k, v) in enumerate(check_group.items()):
            checker = QCheckBox(text=k, parent=self)
            checker.setObjectName('ParamCheckBox')
            checker.setChecked(v)
            layout.addWidget(checker, ii // ncols, ii % ncols)
            self.label2widget[k] = checker
            checker.clicked.connect(self.on_checker_clicked)

    def on_checker_clicked(self):
        new_state_dict = {}
        w = QCheckBox()
        for k, w in self.label2widget.items():
            new_state_dict[k] = w.isChecked()
        self.paramwidget_edited.emit(self.param_key, new_state_dict)


class ParamLineEditor(ConfigLineEdit):
    paramwidget_edited = Signal(str, str)

    def __init__(
        self, param_key: str, force_digital, size="short", *args, **kwargs
    ) -> None:
        super().__init__(*args, **kwargs)
        self.param_key = param_key
        self.setFixedWidth(size2width(size))
        self.setFixedHeight(CONFIG_COMBOBOX_HEIGHT)
        self.textChanged.connect(self.on_text_changed)

        if force_digital:
            validator = QDoubleValidator()
            self.setValidator(validator)

    def on_text_changed(self):
        self.paramwidget_edited.emit(self.param_key, self.text())


class ParamEditor(ConfigTextEdit):
    paramwidget_edited = Signal(str, str)

    def __init__(self, param_key: str, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.param_key = param_key

        if param_key == "chat sample":
            self.setFixedWidth(int(CONFIG_COMBOBOX_LONG * 1.2))
            self.setFixedHeight(200)
        else:
            self.setFixedWidth(CONFIG_COMBOBOX_LONG)
            self.setFixedHeight(100)
        # self.setFixedHeight(CONFIG_COMBOBOX_HEIGHT)
        self.textChanged.connect(self.on_text_changed)

    def on_text_changed(self):
        self.paramwidget_edited.emit(self.param_key, self.text())

    def setText(self, text: str):
        self.setPlainText(text)

    def text(self):
        return self.toPlainText()


class ParamCheckerBox(QWidget):
    checker_changed = Signal(bool)
    paramwidget_edited = Signal(str, str)

    def __init__(self, param_key: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.param_key = param_key
        self.checker = QCheckBox()
        self.checker.setObjectName('ParamCheckBox')
        name_label = ParamNameLabel(param_key)
        hlayout = QHBoxLayout(self)
        hlayout.addWidget(name_label)
        hlayout.addWidget(self.checker)
        hlayout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.checker.stateChanged.connect(self.on_checker_changed)

    def on_checker_changed(self):
        is_checked = self.checker.isChecked()
        self.checker_changed.emit(is_checked)
        checked = "true" if is_checked else "false"
        self.paramwidget_edited.emit(self.param_key, checked)


class ParamCheckBox(QCheckBox):
    paramwidget_edited = Signal(str, bool)

    def __init__(self, param_key: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setObjectName('ParamCheckBox')
        self.param_key = param_key
        self.stateChanged.connect(self.on_checker_changed)

    def on_checker_changed(self):
        self.paramwidget_edited.emit(self.param_key, self.isChecked())


def get_param_display_name(param_key: str, param_dict: dict = None):
    if param_dict is not None and isinstance(param_dict, dict):
        if "display_name" in param_dict:
            return param_dict["display_name"]
    return param_key


class ParamPushButton(QPushButton):
    paramwidget_edited = Signal(str, str)

    def __init__(self, param_key: str, param_dict: dict = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.param_key = param_key
        self.setText(get_param_display_name(param_key, param_dict))
        self.clicked.connect(self.on_clicked)

    def on_clicked(self):
        self.paramwidget_edited.emit(self.param_key, "")


class ParamWidget(QWidget):
    paramwidget_edited = Signal(str, dict)

    def __init__(self, params, scrollWidget: QWidget = None, exclude_keys=None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._exclude_keys = set(exclude_keys or [])
        layout = QHBoxLayout(self)
        self.param_layout = param_layout = QGridLayout()
        param_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        param_layout.setContentsMargins(0, 0, 0, 0)
        param_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        # Give the label column a uniform minimum width so controls in the
        # second column line up across different parameter names.
        param_layout.setColumnMinimumWidth(0, 160)
        param_layout.setColumnStretch(1, 1)
        layout.addLayout(param_layout)
        layout.addStretch(-1)

        if "description" in params:
            self.setToolTip(self.tr(params["description"]))

        for ii, param_key in enumerate(params):
            if param_key == "description" or param_key.startswith("_") or param_key in self._exclude_keys:
                continue
            display_param_name = param_key
            param_dict = None

            require_label = True
            is_str = isinstance(params[param_key], str)
            is_digital = isinstance(params[param_key], float) or isinstance(
                params[param_key], int
            )
            param_widget = None

            if isinstance(params[param_key], bool):
                param_widget = ParamCheckBox(param_key)
                val = params[param_key]
                param_widget.setChecked(val)
                param_widget.paramwidget_edited.connect(self.on_paramwidget_edited)

            elif is_str or is_digital:
                param_widget = ParamLineEditor(param_key, force_digital=is_digital)
                val = params[param_key]
                if is_digital:
                    val = str(val)
                param_widget.setText(val)
                param_widget.paramwidget_edited.connect(self.on_paramwidget_edited)

            elif isinstance(params[param_key], dict):
                param_dict = params[param_key]
                display_param_name = get_param_display_name(param_key, param_dict)
                value = params[param_key]["value"]
                param_widget = None  # Ensure initialization
                param_type = (
                    param_dict["type"] if "type" in param_dict else "line_editor"
                )
                flush_btn = param_dict.get("flush_btn", False)
                path_selector = param_dict.get("path_selector", False)
                param_size = param_dict.get("size", "short")
                if param_type == "selector":
                    if "url" in param_key:
                        size = size2width("median")
                    else:
                        size = size2width(param_size)

                    param_widget = ParamComboBox(
                        param_key,
                        param_dict["options"],
                        size=size,
                        scrollWidget=scrollWidget,
                        flush_btn=flush_btn,
                        path_selector=path_selector,
                    )

                    if param_key == "device" and DEFAULT_DEVICE == "cpu":
                        param_dict["value"] = "cpu"
                        d_idx = 0
                        for device in param_dict["options"]:
                            if device in GPUINTENSIVE_SET:
                                model = param_widget.model()
                                item = model.item(d_idx, 0)
                                item.setEnabled(False)
                            d_idx += 1
                    param_widget.setCurrentText(str(value))
                    param_widget.setEditable(param_dict.get("editable", False))

                elif param_type == "editor":
                    param_widget = ParamEditor(param_key)
                    param_widget.setText(value)

                elif param_type == "checkbox":
                    param_widget = ParamCheckBox(param_key)
                    if isinstance(value, str):
                        value = value.lower().strip() == "true"
                        params[param_key]["value"] = value
                    param_widget.setChecked(value)

                elif param_type == "pushbtn":
                    param_widget = ParamPushButton(param_key, param_dict)
                    require_label = False

                elif param_type == "line_editor":
                    param_widget = ParamLineEditor(param_key, force_digital=is_digital)
                    param_widget.setText(str(value))

                elif param_type == "check_group":
                    param_widget = ParamCheckGroup(param_key, check_group=value)

                if param_widget is not None:
                    param_widget.paramwidget_edited.connect(self.on_paramwidget_edited)

            if (
                param_widget is not None
                and param_dict is not None
                and "description" in param_dict
            ):
                param_widget.setToolTip(self.tr(param_dict["description"]))

            widget_idx = 0
            if require_label:
                param_label = ParamNameLabel(display_param_name)
                param_label.setWordWrap(True)
                if param_dict is not None and "description" in param_dict:
                    param_label.setToolTip(self.tr(param_dict["description"]))

                # label_above: place label above widget spanning full grid width.
                # Used for long-form editors (chat sample, prompts, etc.).
                if (
                    param_dict is not None
                    and param_dict.get("label_above", False)
                    and param_widget is not None
                ):
                    row_widget = QWidget()
                    row_widget.setObjectName("ParamLabelAboveRow")
                    row_layout = QVBoxLayout(row_widget)
                    row_layout.setContentsMargins(0, 0, 0, 0)
                    row_layout.setSpacing(4)
                    row_layout.addWidget(param_label)
                    row_layout.addWidget(param_widget)
                    param_layout.addWidget(row_widget, ii, 0, 1, 2)
                    continue

                param_layout.addWidget(param_label, ii, 0)
                widget_idx = 1
            if param_widget is not None:
                pw_lo = None
                if hasattr(param_widget, "flush_btn") or hasattr(
                    param_widget, "path_select_btn"
                ):
                    pw_lo = QHBoxLayout()
                    pw_lo.addWidget(param_widget)
                if hasattr(param_widget, "flush_btn"):
                    pw_lo.addWidget(param_widget.flush_btn)
                    param_widget.flushbtn_clicked.connect(self.on_flushbtn_clicked)
                if hasattr(param_widget, "path_select_btn"):
                    pw_lo.addWidget(param_widget.path_select_btn)
                    param_widget.pathbtn_clicked.connect(self.on_pathbtn_clicked)
                if pw_lo is None:
                    param_layout.addWidget(param_widget, ii, widget_idx)
                else:
                    param_layout.addLayout(pw_lo, ii, widget_idx)
            else:
                v = params[param_key]
                raise ValueError(
                    f"Failed to initialize widget for key-value pair: {param_key}-{v}"
                )

    def on_flushbtn_clicked(self):
        paramw: ParamComboBox = self.sender()
        content_dict = {"content": "", "widget": paramw, "flush": True}
        self.paramwidget_edited.emit(paramw.param_key, content_dict)

    def on_pathbtn_clicked(self):
        paramw: ParamComboBox = self.sender()
        content_dict = {"content": "", "widget": paramw, "select_path": True}
        self.paramwidget_edited.emit(paramw.param_key, content_dict)

    def on_paramwidget_edited(self, param_key, param_content):
        content_dict = {"content": param_content}
        self.paramwidget_edited.emit(param_key, content_dict)


class ModuleParseWidgets(QWidget):
    def addModulesParamWidgets(self, ocr_instance):
        self.params = ocr_instance.get_params()
        self.on_module_changed()

    def on_module_changed(self):
        self.updateModuleParamWidget()

    def updateModuleParamWidget(self):
        widget = ParamWidget(self.params, scrollWidget=self)
        layout = QVBoxLayout()
        layout.addWidget(widget)
        self.setLayout(layout)


class ModuleConfigParseWidget(QWidget):
    module_changed = Signal(str)
    paramwidget_edited = Signal(str, dict)

    def __init__(
        self,
        module_name: str,
        get_valid_module_keys: Callable,
        scrollWidget: QWidget,
        add_from: int = 1,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.get_valid_module_keys = get_valid_module_keys
        self.module_combobox = ConfigComboBox(scrollWidget=scrollWidget)
        self.params_layout = QHBoxLayout()
        self.params_layout.setContentsMargins(0, 0, 0, 0)

        p_layout = QHBoxLayout()
        p_layout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.module_label = ParamNameLabel(module_name)
        p_layout.addWidget(self.module_label)
        p_layout.addWidget(self.module_combobox)
        p_layout.addStretch(-1)
        self.p_layout = p_layout

        layout = QVBoxLayout(self)
        self.param_widget_map = {}
        layout.addLayout(p_layout)
        layout.addWidget(ConfigSectionHeader(self.tr("Parameters")))
        layout.addLayout(self.params_layout)
        layout.setSpacing(14)
        self.vlayout = layout

        self.visibleWidget: QWidget = None
        self.module_dict: dict = {}

    def addModulesParamWidgets(
        self, module_dict: dict, dep_notes: dict[str, str] = None
    ):
        """Populate the module combobox.

        ``dep_notes`` is an optional ``{module_name: description_or_None}`` dict.
        Modules with a non-None description are grouped under a "needs deps"
        separator and get a tooltip showing what dependencies they require.
        """
        invalid_module_keys = []
        valid_modulekeys = self.get_valid_module_keys()

        num_widgets_before = len(self.param_widget_map)

        # Categorise: skip → normal → needs-deps
        skip_keys: list[str] = []
        normal_keys: list[str] = []
        dep_keys: list[str] = []
        for module in module_dict:
            if module not in valid_modulekeys:
                invalid_module_keys.append(module)
                continue
            if module in self.param_widget_map:
                LOGGER.warning(f"duplicated module key: {module}")
                continue
            if module.startswith("none") or module.startswith("None"):
                skip_keys.append(module)
            elif dep_notes and module in dep_notes and dep_notes[module]:
                dep_keys.append(module)
            else:
                normal_keys.append(module)

        # Build combobox: skip → separator → normal + needs-deps (merged)
        for module in skip_keys:
            self.module_combobox.addItem(module)

        remaining = normal_keys + dep_keys
        if remaining:
            self.module_combobox.insertSeparator(self.module_combobox.count())
            for module in remaining:
                self.module_combobox.addItem(module)
                if module in dep_keys:
                    hint = dep_notes.get(module, "")
                    if hint:
                        idx = self.module_combobox.count() - 1
                        self.module_combobox.setItemData(
                            idx, hint, Qt.ItemDataRole.ToolTipRole
                        )

        # Register param slots for all modules (keyed by module name)
        ordered_module_keys = skip_keys + normal_keys + dep_keys
        for module in ordered_module_keys:
            params = module_dict[module]
            if params is not None:
                self.param_widget_map[module] = None

        if len(invalid_module_keys) > 0:
            LOGGER.warning(f"Invalid module keys: {invalid_module_keys}")
            for ik in invalid_module_keys:
                module_dict.pop(ik)

        self.module_dict = module_dict

        num_widgets_after = len(self.param_widget_map)
        if num_widgets_before == 0 and num_widgets_after > 0:
            self.on_module_changed()
            self.module_combobox.currentTextChanged.connect(self.on_module_changed)

    def setModule(self, module: str):
        self.blockSignals(True)
        self.module_combobox.setCurrentText(module)
        self.updateModuleParamWidget()
        self.blockSignals(False)

    def updateModuleParamWidget(self):
        module = self.module_combobox.currentText()
        if self.visibleWidget is not None:
            self.visibleWidget.hide()
        if module in self.param_widget_map:
            widget: QWidget = self.param_widget_map[module]
            if widget is None:
                # lazy load widgets
                params = self.module_dict[module]
                widget = ParamWidget(params, scrollWidget=self)
                widget.paramwidget_edited.connect(self.paramwidget_edited)
                self.param_widget_map[module] = widget
                self.params_layout.addWidget(widget)
            else:
                widget.show()
            self.visibleWidget = widget

    def on_module_changed(self):
        self.updateModuleParamWidget()
        self.module_changed.emit(self.module_combobox.currentText())


class TranslatorConfigPanel(ModuleConfigParseWidget):
    """Translator configuration panel.

    Extends the base module panel with:
    - Source / target language selectors.
    - Dedicated ``active_profile`` section (extracted from ParamWidget for
      visual prominence) when the selected translator supports it.
    """

    navigate_to_llm_profile = Signal()

    def __init__(
        self, module_name, scrollWidget: QWidget = None, *args, **kwargs
    ) -> None:
        super().__init__(
            module_name,
            GET_VALID_TRANSLATORS,
            scrollWidget=scrollWidget,
            *args,
            **kwargs,
        )
        self.translator_changed = self.module_changed

        # ── Source / Target languages ────────────────────────────
        self.source_combobox = ConfigComboBox(scrollWidget=scrollWidget)
        self.target_combobox = ConfigComboBox(scrollWidget=scrollWidget)

        st_layout = QHBoxLayout()
        st_layout.setSpacing(15)
        st_layout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        st_layout.addWidget(ParamNameLabel(self.tr("Source")))
        st_layout.addWidget(self.source_combobox)
        st_layout.addWidget(ParamNameLabel(self.tr("Target")))
        st_layout.addWidget(self.target_combobox)

        self.vlayout.insertLayout(1, st_layout)

        # ── Active Profile section ───────────────────────────────
        profile_section = QWidget()
        ps_layout = QVBoxLayout(profile_section)
        ps_layout.setContentsMargins(0, 0, 0, 0)
        ps_layout.setSpacing(4)

        ps_header = ConfigSectionHeader(self.tr("API Profile"))
        ps_layout.addWidget(ps_header)

        # Combo + button row
        profile_row = QHBoxLayout()
        profile_row.setSpacing(6)

        self._profile_combo = ConfigComboBox(scrollWidget=scrollWidget)
        self._profile_combo.setFixedWidth(CONFIG_COMBOBOX_LONG)
        self._profile_combo.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )

        manage_btn = QPushButton(self.tr("Manage…"))
        manage_btn.setObjectName("ConfigButton")
        manage_btn.clicked.connect(self._on_manage_profiles)

        profile_row.addWidget(self._profile_combo)
        profile_row.addWidget(manage_btn)
        profile_row.addStretch()

        ps_layout.addLayout(profile_row)

        self.vlayout.insertWidget(2, profile_section)
        self._profile_section = profile_section
        self._profile_section.setVisible(False)

        self._profile_combo.currentTextChanged.connect(self._on_profile_changed)

    # ── Public ───────────────────────────────────────────────────

    def finishSetTranslator(self, translator: BaseTranslator):
        self.source_combobox.blockSignals(True)
        self.target_combobox.blockSignals(True)
        self.module_combobox.blockSignals(True)

        self.source_combobox.clear()
        self.target_combobox.clear()

        self.source_combobox.addItems(translator.supported_src_list)
        self.target_combobox.addItems(translator.supported_tgt_list)
        self.module_combobox.setCurrentText(translator.name)
        self.source_combobox.setCurrentText(translator.lang_source)
        self.target_combobox.setCurrentText(translator.lang_target)
        self.updateModuleParamWidget()
        self.source_combobox.blockSignals(False)
        self.target_combobox.blockSignals(False)
        self.module_combobox.blockSignals(False)

    # ── Overrides ────────────────────────────────────────────────

    def updateModuleParamWidget(self):
        """Filter out ``active_profile`` — handled by dedicated section."""
        module = self.module_combobox.currentText()
        if self.visibleWidget is not None:
            self.visibleWidget.hide()

        self._refresh_profile_section()

        if module in self.param_widget_map:
            widget = self.param_widget_map[module]
            if widget is None:
                params = self.module_dict[module]
                filtered = {
                    k: v for k, v in params.items() if k != "active_profile"
                }
                widget = ParamWidget(
                    filtered, scrollWidget=self, exclude_keys={"active_profile"}
                )
                widget.paramwidget_edited.connect(self.paramwidget_edited)
                self.param_widget_map[module] = widget
                self.params_layout.addWidget(widget)
            else:
                widget.show()
            self.visibleWidget = widget

    # ── Profile section ──────────────────────────────────────────

    def _refresh_profile_section(self):
        """Show/hide and populate the profile combo for the current translator."""
        module = self.module_combobox.currentText()
        params = self.module_dict.get(module)
        has_profile = bool(params and "active_profile" in params)
        self._profile_section.setVisible(has_profile)
        if not has_profile:
            return

        active_cfg = params["active_profile"]
        options = active_cfg.get("options", [])
        value = active_cfg.get("value", "")
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        self._profile_combo.addItems(options)
        self._profile_combo.setCurrentText(value)
        self._profile_combo.blockSignals(False)

    def _on_profile_changed(self, profile_name: str):
        """Emit param change through the standard path so module_manager
        persists it and updates the translator instance."""
        if profile_name:
            self.paramwidget_edited.emit(
                "active_profile", {"content": profile_name}
            )

    def _on_manage_profiles(self):
        self.navigate_to_llm_profile.emit()


class InpaintConfigPanel(ModuleConfigParseWidget):
    def __init__(
        self, module_name: str, scrollWidget: QWidget = None, *args, **kwargs
    ) -> None:
        super().__init__(
            module_name,
            GET_VALID_INPAINTERS,
            scrollWidget=scrollWidget,
            *args,
            **kwargs,
        )
        self.inpainter_changed = self.module_changed
        self.setInpainter = self.setModule
        self.needInpaintChecker = ParamCheckerBox(
            self.tr(
                "Let the program decide whether it is necessary to use the selected inpaint method."
            )
        )
        self.vlayout.addWidget(self.needInpaintChecker)

    def showEvent(self, e) -> None:
        self.p_layout.insertWidget(1, self.module_combobox)
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self.p_layout.removeWidget(self.module_combobox)
        return super().hideEvent(e)


class TextDetectConfigPanel(ModuleConfigParseWidget):
    def __init__(
        self, module_name: str, scrollWidget: QWidget = None, *args, **kwargs
    ) -> None:
        super().__init__(
            module_name,
            GET_VALID_TEXTDETECTORS,
            scrollWidget=scrollWidget,
            *args,
            **kwargs,
        )
        self.detector_changed = self.module_changed
        self.setDetector = self.setModule
        self.keep_existing_checker = QCheckBox(text=self.tr("Keep Existing Lines"))
        self.keep_existing_checker.setObjectName('ParamCheckBox')
        self.p_layout.insertWidget(2, self.keep_existing_checker)


class OCRConfigPanel(ModuleConfigParseWidget):
    def __init__(
        self, module_name: str, scrollWidget: QWidget = None, *args, **kwargs
    ) -> None:
        super().__init__(
            module_name, GET_VALID_OCR, scrollWidget=scrollWidget, *args, **kwargs
        )
        self.ocr_changed = self.module_changed
        self.setOCR = self.setModule
