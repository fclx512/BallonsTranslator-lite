"""LLM Profile 卡片式设置页（A 方案·精简版）。

形态对齐上游 BallonsTranslator 1.5.14 的 ``ui/llm_profile_widgets.py``：
卡片列表 + 折叠详情 + 能力徽章分节，便于后续对照上游做定制改动。
数据层仍走 ``utils/profile_manager.py`` 的 dict 模型（name 作主键、
``pcfg.module.model_profiles`` 存 JSON 字符串），不引入上游的
``LLMProfile`` dataclass / SecretStore / id 主键。

上游对应关系：

    LLMProfilesWidget    -> LLMProfileListWidget
    ProfileCardWidget    -> LLMProfileCardWidget
    ProfileDetailsWidget -> LLMProfileDetailsWidget
    CapabilityBadgeLabel -> LLMProfileBadge

与上游的取舍：本版不做双击就地改名与右键菜单；模型选择是「摘要行下拉 +
手动增删 + Fetch Models 多选添加」，但**不预置任何供应商模型表**（清单
只由抓取或手填产生）。字段集合与消费者行为保持不变。
"""

from typing import Dict, List, Optional, Tuple

import httpx
from qtpy.QtCore import QCoreApplication, Qt, Signal
from qtpy.QtGui import QFontMetrics, QIcon
from qtpy.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.custom_widget import ConfigComboBox, ConfigLineEdit
from ui.icon_rendering import render_svg_pixmap
from ui.misc import get_theme_color, themed_icon_path
from ui.module_parse_widgets import ParamWidget
from utils.profile_manager import (
    FilterableListDialog,
    NetWorker,
    SAMPLE_PROFILES,
    fetch_image_models,
    get_default_profile_name,
    get_profiles_raw,
    load_profiles,
    probe_connection,
    probe_model_list,
    remember_model_option,
    save_all_profiles,
    set_default_profile,
)
from utils.shared import LINEEDIT_FIXHEIGHT

# ── 参数定义（声明式；镜像上游 PROFILE_COMMON_PARAM_DEFS / PROFILE_MODALITY_PARAM_DEFS）──

# (字段 key, ParamWidget 类型)
# model / image_model 不在这里——它们由摘要行的模型下拉负责（见 _ModelSelector）。
# api_host / api_key 不在这里——它们由连接信息块单独承载（见 _ConnectionBlock）。
PROFILE_COMMON_PARAM_DEFS: List[Tuple[str, str]] = [
    ("proxy", "line_editor"),
    ("requests_per_minute", "line_editor"),
    ("delay", "line_editor"),
    ("temperature", "line_editor"),
    ("top_p", "line_editor"),
    ("max_tokens", "line_editor"),
    ("reasoning_effort", "selector"),
    ("return_json_schema", "checkbox"),
]

# 连接信息块承载的字段（顺序即显示顺序）；图像端口随图像能力显隐。
CONNECTION_PARAM_KEYS: Tuple[str, ...] = ("api_host", "api_key", "image_base_url")
CONNECTION_PLACEHOLDERS: Dict[str, str] = {
    "api_host": "https://api.openai.com/v1",
    "api_key": "sk-...",
    "image_base_url": "https://api.meshy.ai/openapi/v1/image-to-image",
}

# 能力分节：文本恒显，视觉 / 图像由徽章门控
PROFILE_SECTION_PARAM_DEFS: Dict[str, List[Tuple[str, str]]] = {
    "text": [
        ("system_prompt", "editor"),
    ],
    "vision": [
        ("ocr_prompt", "editor"),
        ("ocr_system_prompt", "editor"),
        ("ocr_detail_level", "selector"),
        ("ocr_max_response_tokens", "line_editor"),
    ],
    "image": [
        ("image_prompt", "editor"),
    ],
}

# 模型字段 → 清单字段（摘要行下拉用）
MODEL_OPTION_FIELDS: Dict[str, str] = {
    "model": "model_options",
    "image_model": "image_model_options",
}

# 写回时的类型转换（对应上游 get_type_hints(LLMProfile) 的作用）
PROFILE_FIELD_TYPES: Dict[str, type] = {
    "requests_per_minute": int,
    "ocr_max_response_tokens": int,
    "temperature": float,
    "top_p": float,
    "delay": float,
    "return_json_schema": bool,
}

_PARAM_DEFAULTS = {
    "requests_per_minute": 20,
    "temperature": 0.1,
    "top_p": 1.0,
    "delay": 0.3,
    "ocr_max_response_tokens": 4096,
}

# 逐键写回时要去掉首尾空白的纯字符串字段
_STRIP_STRING_KEYS = {
    "api_host",
    "api_key",
    "model",
    "proxy",
    "max_tokens",
    "image_base_url",
    "image_model",
}

# 标签在字面量处显式标注上下文——``self.tr(variable)`` 间接查表工具链看不见。
PROFILE_PARAM_LABELS: Dict[str, str] = {
    "api_host": QCoreApplication.translate("LLMProfileCardWidget", "Host"),
    "api_key": QCoreApplication.translate("LLMProfileCardWidget", "API Key"),
    "model": QCoreApplication.translate("LLMProfileCardWidget", "Model"),
    "proxy": QCoreApplication.translate("LLMProfileCardWidget", "Proxy"),
    "requests_per_minute": QCoreApplication.translate(
        "LLMProfileCardWidget", "Requests/min"
    ),
    "delay": QCoreApplication.translate("LLMProfileCardWidget", "Delay (s)"),
    "temperature": QCoreApplication.translate("LLMProfileCardWidget", "Temperature"),
    "top_p": QCoreApplication.translate("LLMProfileCardWidget", "Top P"),
    "max_tokens": QCoreApplication.translate("LLMProfileCardWidget", "Max Tokens"),
    "reasoning_effort": QCoreApplication.translate(
        "LLMProfileCardWidget", "Reasoning Effort"
    ),
    "return_json_schema": QCoreApplication.translate(
        "LLMProfileCardWidget", "Return JSON Schema"
    ),
    "system_prompt": QCoreApplication.translate(
        "LLMProfileCardWidget", "Instructions"
    ),
    "ocr_prompt": QCoreApplication.translate("LLMProfileCardWidget", "OCR Prompt"),
    "ocr_system_prompt": QCoreApplication.translate(
        "LLMProfileCardWidget", "OCR System Prompt"
    ),
    "ocr_detail_level": QCoreApplication.translate(
        "LLMProfileCardWidget", "Detail Level"
    ),
    "ocr_max_response_tokens": QCoreApplication.translate(
        "LLMProfileCardWidget", "Max Tokens"
    ),
    "image_base_url": QCoreApplication.translate(
        "LLMProfileCardWidget", "Image Endpoint"
    ),
    "image_model": QCoreApplication.translate("LLMProfileCardWidget", "Image Model"),
    "image_prompt": QCoreApplication.translate(
        "LLMProfileCardWidget", "Image Prompt"
    ),
}

PROFILE_PARAM_DESCRIPTIONS: Dict[str, str] = {
    "api_host": QCoreApplication.translate(
        "LLMProfileCardWidget", "OpenAI-compatible API base URL."
    ),
    "api_key": QCoreApplication.translate(
        "LLMProfileCardWidget", "API key for this provider. Stored locally."
    ),
    "model": QCoreApplication.translate(
        "LLMProfileCardWidget", "Model id used for translation and OCR."
    ),
    "proxy": QCoreApplication.translate(
        "LLMProfileCardWidget", "Optional HTTP proxy, e.g. http://127.0.0.1:7890."
    ),
    "requests_per_minute": QCoreApplication.translate(
        "LLMProfileCardWidget", "Maximum requests per minute. 0 = unlimited."
    ),
    "delay": QCoreApplication.translate(
        "LLMProfileCardWidget", "Minimum delay between requests, in seconds."
    ),
    "temperature": QCoreApplication.translate(
        "LLMProfileCardWidget", "Sampling temperature."
    ),
    "top_p": QCoreApplication.translate("LLMProfileCardWidget", "Top-p sampling."),
    "max_tokens": QCoreApplication.translate(
        "LLMProfileCardWidget",
        "Maximum response tokens. Leave empty for no limit.",
    ),
    "reasoning_effort": QCoreApplication.translate(
        "LLMProfileCardWidget",
        "Override the model's reasoning effort. Default lets the API decide.",
    ),
    "return_json_schema": QCoreApplication.translate(
        "LLMProfileCardWidget",
        "Request responses with the translation JSON schema. Disable if the provider rejects it.",
    ),
    "system_prompt": QCoreApplication.translate(
        "LLMProfileCardWidget",
        "Optional custom instructions appended to the translation system prompt.",
    ),
    "ocr_prompt": QCoreApplication.translate(
        "LLMProfileCardWidget",
        "OCR prompt; the language placeholder is replaced with the source language.",
    ),
    "ocr_system_prompt": QCoreApplication.translate(
        "LLMProfileCardWidget", "Optional system prompt for OCR."
    ),
    "ocr_detail_level": QCoreApplication.translate(
        "LLMProfileCardWidget",
        "Image detail level sent to vision-capable providers.",
    ),
    "ocr_max_response_tokens": QCoreApplication.translate(
        "LLMProfileCardWidget", "Maximum tokens for the OCR response."
    ),
    "image_base_url": QCoreApplication.translate(
        "LLMProfileCardWidget",
        "Image editing endpoint. Leave empty to reuse the API host above.",
    ),
    "image_model": QCoreApplication.translate(
        "LLMProfileCardWidget", "Model id used for online inpainting."
    ),
    "image_prompt": QCoreApplication.translate(
        "LLMProfileCardWidget",
        "Instructions sent to the image model for cleanup.",
    ),
}

_REASONING_DEFAULT = QCoreApplication.translate("LLMProfileCardWidget", "Default")
_SELECTOR_OPTIONS: Dict[str, List[str]] = {
    "reasoning_effort": [
        _REASONING_DEFAULT,
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ],
    "ocr_detail_level": ["auto", "low", "high"],
}

PROFILE_SECTION_TITLES: Dict[str, str] = {
    "text": QCoreApplication.translate("LLMProfileCardWidget", "Text"),
    "vision": QCoreApplication.translate("LLMProfileCardWidget", "Vision (OCR)"),
    "image": QCoreApplication.translate(
        "LLMProfileCardWidget", "Image (Inpainting)"
    ),
}

CONNECTION_SECTION_TITLE = QCoreApplication.translate(
    "LLMProfileDetailsWidget", "Connection"
)
GENERATION_SECTION_TITLE = QCoreApplication.translate(
    "LLMProfileDetailsWidget", "Generation"
)


def _param_defs(defs: List[Tuple[str, str]], profile: Dict) -> Dict[str, dict]:
    """把 (key, type) 列表展开成 ParamWidget 需要的 params dict。"""
    out: Dict[str, dict] = {}
    for key, type_ in defs:
        value = profile.get(key, _PARAM_DEFAULTS.get(key, ""))
        param: dict = {
            "type": type_,
            "display_name": PROFILE_PARAM_LABELS.get(key, key),
            "description": PROFILE_PARAM_DESCRIPTIONS.get(key, ""),
        }
        if type_ == "selector":
            param["options"] = list(_SELECTOR_OPTIONS.get(key, []))
            param["value"] = _selector_display(key, value)
        elif type_ == "checkbox":
            param["value"] = bool(value)
        elif type_ == "editor":
            param["value"] = str(value or "")
            param["label_above"] = True
        else:
            param["value"] = "" if value is None else str(value)
        out[key] = param
    return out


def _selector_display(key: str, value) -> str:
    if key == "reasoning_effort" and not value:
        return _REASONING_DEFAULT
    return str(value)


class _ElidedLabel(QLabel):
    """QLabel that elides in the middle when the text overflows.

    Only elides when the text is actually wider than the label — eliding at an
    exactly-matching width truncates on integer-rounding fonts (see
    docs/基础速查/经验教训.md 的 elide 刀刃教训).
    """

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self._full = text or ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setToolTip(self._full)
        self._apply()

    def setText(self, text: str) -> None:
        self._full = text or ""
        self.setToolTip(self._full)
        self._apply()

    def fullText(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply()

    def _apply(self) -> None:
        fm = QFontMetrics(self.font())
        if self.width() > 0 and fm.horizontalAdvance(self._full) > self.width():
            super().setText(
                fm.elidedText(
                    self._full, Qt.TextElideMode.ElideMiddle, self.width()
                )
            )
        else:
            super().setText(self._full)


class LLMProfileBadge(QLabel):
    """能力徽章：点击切换该 profile 是否支持该模态（文本徽章恒亮、不可点）。"""

    clicked = Signal()

    def __init__(
        self,
        icon_name: str,
        accent_key: str,
        clickable: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("LLMProfileBadge")
        self.setFixedSize(18, 18)
        self._icon_name = icon_name
        self._accent_key = accent_key
        self._clickable = clickable
        self._active = False
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh()

    def set_active(self, active: bool) -> None:
        active = bool(active)
        if self._active == active:
            return
        self._active = active
        self._refresh()

    def is_active(self) -> bool:
        return self._active

    def _refresh(self) -> None:
        if self._active:
            # 图标本身按模态强调色着色，再叠一层同色淡底。
            tint = get_theme_color(alpha=45, key=self._accent_key)
            kwargs = {
                "override_fill": get_theme_color(key=self._accent_key).name(),
                "inset": 2,
                "background_rgba": tint.getRgb(),
                "background_radius": 5,
            }
        else:
            kwargs = {
                "override_fill": get_theme_color(
                    key="@disabledForegroundColor"
                ).name()
            }
        self.setPixmap(
            render_svg_pixmap(
                themed_icon_path(self._icon_name),
                18,
                18,
                self.devicePixelRatioF(),
                **kwargs,
            )
        )

    def mouseReleaseEvent(self, event) -> None:
        if self._clickable and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def _section_header(title: str, parent: QWidget) -> QWidget:
    """居中标题 + 两侧细线（对齐上游 LLMProfileDetailSection* 的视觉）。"""
    header = QWidget(parent)
    header.setObjectName("LLMProfileDetailSectionHeader")
    layout = QHBoxLayout(header)
    layout.setContentsMargins(0, 8, 0, 4)
    layout.setSpacing(8)
    label = QLabel(title, header)
    label.setObjectName("LLMProfileDetailSectionTitle")
    left_line = QFrame(header)
    right_line = QFrame(header)
    for line in (left_line, right_line):
        line.setObjectName("LLMProfileDetailSectionLine")
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Plain)
        line.setFixedHeight(1)
        line.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
    layout.addWidget(left_line, 1)
    layout.addWidget(label, 0)
    layout.addWidget(right_line, 1)
    return header


class _ConnectionBlock(QFrame):
    """连接信息块：主机地址 / API Key / 图像端口。

    这三个字段是 profile 里最要紧、也最需要长输入框的配置，因此从其余杂项
    参数里独立出来：标签在上、输入框整行宽且加高，外层容器带强调边框。
    图像端口仅在图像能力开启时出现（与 ``image`` 分节同步显隐）。
    """

    edited = Signal(str, dict)

    def __init__(self, values: Optional[Dict[str, str]] = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("LLMProfileConnectionBlock")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)

        self._edits: Dict[str, ConfigLineEdit] = {}
        self._rows: Dict[str, QWidget] = {}
        values = values or {}
        for key in CONNECTION_PARAM_KEYS:
            row = QWidget(self)
            row.setObjectName("LLMProfileConnectionRow")
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)
            field_label = QLabel(PROFILE_PARAM_LABELS.get(key, key), row)
            field_label.setObjectName("LLMProfileConnectionLabel")
            edit = ConfigLineEdit(parent=row)
            edit.setObjectName("LLMProfileConnectionEdit")
            edit.setFixedHeight(LINEEDIT_FIXHEIGHT)
            edit.setPlaceholderText(CONNECTION_PLACEHOLDERS.get(key, ""))
            edit.setToolTip(PROFILE_PARAM_DESCRIPTIONS.get(key, ""))
            edit.setText(str(values.get(key, "") or ""))
            edit.textChanged.connect(
                lambda text, key=key: self.edited.emit(key, {"content": text})
            )
            row_layout.addWidget(field_label)
            row_layout.addWidget(edit)
            layout.addWidget(row)
            self._edits[key] = edit
            self._rows[key] = row

        self.set_image_endpoint_visible(False)

    def set_image_endpoint_visible(self, visible: bool) -> None:
        row = self._rows.get("image_base_url")
        if row is not None:
            row.setVisible(bool(visible))

    def edit_for(self, key: str) -> Optional[ConfigLineEdit]:
        return self._edits.get(key)


class LLMProfileDetailsWidget(QWidget):
    """展开态详情：连接信息 + 通用参数 + 三个能力分节，每节一个 ParamWidget。"""

    paramwidget_edited = Signal(str, dict)

    def __init__(
        self,
        common_params: Dict[str, dict],
        sections: Dict[str, Tuple[str, Dict[str, dict]]],
        connection_values: Optional[Dict[str, str]] = None,
        image_endpoint_visible: bool = False,
        scrollWidget: Optional[QWidget] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("LLMProfileDetails")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)
        self.section_widgets: Dict[str, QWidget] = {}

        layout.addWidget(_section_header(CONNECTION_SECTION_TITLE, self))
        self._connection = _ConnectionBlock(connection_values, self)
        self._connection.edited.connect(self.paramwidget_edited.emit)
        self._connection.set_image_endpoint_visible(image_endpoint_visible)
        layout.addWidget(self._connection)

        layout.addWidget(_section_header(GENERATION_SECTION_TITLE, self))
        self._add_params(layout, common_params, scrollWidget)

        for key, (title, params) in sections.items():
            section = QWidget(self)
            section.setObjectName("LLMProfileDetailSection")
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(4)
            section_layout.addWidget(_section_header(title, section))
            self._add_params(section_layout, params, scrollWidget)
            self.section_widgets[key] = section
            layout.addWidget(section)

    def _add_params(
        self,
        layout: QVBoxLayout,
        params: Dict[str, dict],
        scrollWidget: Optional[QWidget],
    ) -> Optional[ParamWidget]:
        if not params:
            return None
        # 传副本：ParamWidget 会原地改写传入 dict（device 覆盖、checkbox 字符串转 bool）。
        copied = {
            key: (dict(value) if isinstance(value, dict) else value)
            for key, value in params.items()
        }
        widget = ParamWidget(copied, scrollWidget=scrollWidget, parent=self)
        widget.paramwidget_edited.connect(self.paramwidget_edited.emit)
        layout.addWidget(widget)
        return widget

    def set_section_visible(self, section_key: str, visible: bool) -> None:
        if section_key == "image":
            self._connection.set_image_endpoint_visible(visible)
        section = self.section_widgets.get(section_key)
        if section is not None:
            section.setVisible(bool(visible))


class _ModelSelector(QWidget):
    """摘要行的模型选择器：下拉 + 手动添加 / 删除当前。

    清单是 profile 自己的 ``model_options`` / ``image_model_options``，不预置
    任何供应商模型名——只由 Fetch Models 多选或这里的 ``+`` 手填产生。
    """

    # (新的清单, 新的当前值)
    changed = Signal(list, str)

    def __init__(
        self,
        label: str,
        placeholder: str,
        add_tip: str,
        remove_tip: str,
        scrollWidget: Optional[QWidget] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("LLMProfileModelRow")
        self._placeholder = placeholder
        self._editing = False
        self._previous = ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        field_label = QLabel(label, self)
        field_label.setObjectName("LLMProfileFieldLabel")
        layout.addWidget(field_label)

        # 宽度按清单内容自适应（AdjustToContents + fix_size=False 的上限兜底），
        # 不再横向拉伸撑满整行——长下拉看着空、也把增删按钮推得太远。
        self.combo = ConfigComboBox(
            fix_size=False, scrollWidget=scrollWidget
        )
        self.combo.setObjectName("LLMProfileModelCombo")
        self.combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.combo.setMinimumWidth(160)
        self.combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.combo.setToolTip(label)
        self.combo.currentTextChanged.connect(self._on_text_changed)
        layout.addWidget(self.combo)

        self._add_btn = QToolButton(self)
        self._add_btn.setObjectName("LLMProfileModelAddButton")
        self._add_btn.setFixedSize(18, 18)
        self._add_btn.setIcon(QIcon(themed_icon_path("add.svg")))
        self._add_btn.setToolTip(add_tip)
        self._add_btn.clicked.connect(self.start_edit)
        layout.addWidget(self._add_btn)

        self._remove_btn = QToolButton(self)
        self._remove_btn.setObjectName("LLMProfileModelRemoveButton")
        self._remove_btn.setFixedSize(18, 18)
        self._remove_btn.setIcon(QIcon(themed_icon_path("titlebar_min.svg")))
        self._remove_btn.setToolTip(remove_tip)
        self._remove_btn.clicked.connect(self._remove_current)
        layout.addWidget(self._remove_btn)
        layout.addStretch()

    # ── 状态 ──

    def options(self) -> List[str]:
        return [self.combo.itemText(i) for i in range(self.combo.count())]

    def current_text(self) -> str:
        return self.combo.currentText()

    def set_options(self, options: List[str], current: str) -> None:
        self._editing = False
        self.combo.blockSignals(True)
        try:
            self.combo.setEditable(False)
            self.combo.clear()
            self.combo.addItems([str(o) for o in options])
            current = str(current or "")
            if current and self.combo.findText(current) < 0:
                self.combo.addItem(current)
            self.combo.setCurrentText(current)
        finally:
            self.combo.blockSignals(False)

    # ── 交互 ──

    def _on_text_changed(self, text: str) -> None:
        if self._editing:
            return
        self.changed.emit(self.options(), str(text))

    def start_edit(self) -> None:
        if self._editing:
            return
        self._editing = True
        self._previous = self.combo.currentText()
        self.combo.setEditable(True)
        editor = self.combo.lineEdit()
        if editor is None:
            self._editing = False
            return
        editor.setPlaceholderText(self._placeholder)
        try:
            editor.editingFinished.disconnect(self._finish_edit)
        except (TypeError, RuntimeError):
            pass
        editor.editingFinished.connect(self._finish_edit)
        self.combo.setEditText("")
        editor.setFocus()
        editor.selectAll()

    def _finish_edit(self) -> None:
        if not self._editing:
            return
        editor = self.combo.lineEdit()
        text = editor.text().strip() if editor is not None else ""
        self._editing = False
        if editor is not None:
            editor.setPlaceholderText("")
        self.combo.setEditable(False)
        if not text:
            self.combo.blockSignals(True)
            self.combo.setCurrentText(self._previous)
            self.combo.blockSignals(False)
            return
        self.combo.blockSignals(True)
        if self.combo.findText(text) < 0:
            self.combo.addItem(text)
        self.combo.setCurrentText(text)
        self.combo.blockSignals(False)
        self.changed.emit(self.options(), text)

    def _remove_current(self) -> None:
        current = self.combo.currentText()
        options = self.options()
        if current not in options:
            return
        index = options.index(current)
        options.pop(index)
        next_value = options[min(index, len(options) - 1)] if options else ""
        self.set_options(options, next_value)
        self.changed.emit(self.options(), next_value)


class LLMProfileCardWidget(QFrame):
    """单张 profile 卡片：折叠态摘要 + 展开态详情 + 能力徽章。"""

    persist_requested = Signal()
    delete_requested = Signal(str)
    use_requested = Signal(str)

    def __init__(
        self, profile: Dict, scrollWidget: Optional[QWidget] = None, parent=None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("LLMProfileCard")
        self.profile = profile
        self._scrollWidget = scrollWidget
        self._details: Optional[LLMProfileDetailsWidget] = None
        self._expanded = False
        self._net_worker: Optional[NetWorker] = None
        self._modal_runner = None
        self._model_selectors: Dict[str, _ModelSelector] = {}
        self._build_ui()
        self.sync_from_profile()

    # ── 构建 ──

    def _build_ui(self) -> None:
        body = QVBoxLayout(self)
        body.setContentsMargins(14, 10, 14, 10)
        body.setSpacing(6)
        self._body_layout = body

        header = QWidget(self)
        header.setObjectName("LLMProfileCardHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)
        self._name_label = QLabel(header)
        self._name_label.setObjectName("LLMProfileName")
        header_layout.addWidget(self._name_label)
        # 「使用」= 记成全局激活的 profile（pcfg.module.default_profile）：
        # 翻译 / OCR / 修图的选择器默认跟随它，各自仍可单独覆盖。
        self._use_btn = QPushButton(self.tr("Use"), header)
        self._use_btn.setObjectName("LLMProfileUseButton")
        self._use_btn.setCheckable(True)
        self._use_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._use_btn.setToolTip(
            self.tr(
                "Make this the default profile for translation, OCR and inpainting."
            )
        )
        self._use_btn.clicked.connect(
            lambda: self.use_requested.emit(self.profile.get("name", ""))
        )
        header_layout.addWidget(self._use_btn)
        header_layout.addStretch()
        self._edit_btn = QToolButton(header)
        self._edit_btn.setObjectName("LLMProfileConfigButton")
        self._edit_btn.setFixedSize(22, 22)
        self._edit_btn.setIcon(QIcon(themed_icon_path("edit.svg")))
        self._edit_btn.setToolTip(self.tr("Edit"))
        self._edit_btn.clicked.connect(self.toggle_expanded)
        header_layout.addWidget(self._edit_btn)
        self._delete_btn = QToolButton(header)
        self._delete_btn.setObjectName("LLMProfileDeleteButton")
        self._delete_btn.setFixedSize(18, 18)
        self._delete_btn.setIcon(QIcon(themed_icon_path("titlebar_close.svg")))
        self._delete_btn.setToolTip(self.tr("Delete"))
        self._delete_btn.clicked.connect(
            lambda: self.delete_requested.emit(self.profile.get("name", ""))
        )
        header_layout.addWidget(self._delete_btn)
        body.addWidget(header)

        summary = QWidget(self)
        summary.setObjectName("LLMProfileSummaryRow")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(8)
        self._text_badge = LLMProfileBadge(
            "text.svg", "@accentTranslate", clickable=False, parent=summary,
        )
        self._text_badge.setToolTip(
            self.tr("Text support is always available.")
        )
        self._vision_badge = LLMProfileBadge(
            "eye.svg", "@accentOCR", parent=summary
        )
        self._vision_badge.setToolTip(
            self.tr("Toggle vision support (this profile then appears in the OCR selector).")
        )
        self._vision_badge.clicked.connect(self._toggle_vision_support)
        self._image_badge = LLMProfileBadge(
            "image.svg", "@accentInpaint", parent=summary
        )
        self._image_badge.setToolTip(
            self.tr("Toggle image support (this profile then appears in the online inpainter selector).")
        )
        self._image_badge.clicked.connect(self._toggle_image_support)
        self._summary_label = _ElidedLabel("", summary)
        self._summary_label.setObjectName("LLMProfileSummaryText")
        self._key_icon = QLabel(summary)
        self._key_icon.setObjectName("LLMProfileKeyStatusIcon")
        self._key_icon.setFixedSize(16, 16)
        summary_layout.addWidget(self._text_badge)
        summary_layout.addWidget(self._vision_badge)
        summary_layout.addWidget(self._image_badge)
        summary_layout.addWidget(self._summary_label, 1)
        summary_layout.addWidget(self._key_icon)
        body.addWidget(summary)

        # 模型选择：上游把下拉放在摘要行、展开前就能切；这里同样常显。
        self._model_rows: Dict[str, QWidget] = {}
        self._build_model_row(
            body,
            "model",
            PROFILE_PARAM_LABELS.get("model", "Model"),
            self.tr("Model name"),
            self.tr("Add model"),
            self.tr("Delete current model"),
        )
        self._build_model_row(
            body,
            "image_model",
            PROFILE_PARAM_LABELS.get("image_model", "Image Model"),
            self.tr("Image model name"),
            self.tr("Add image model"),
            self.tr("Delete current image model"),
        )

        self._actions = self._build_actions()
        body.addWidget(self._actions)
        self._actions.hide()

    def _build_model_row(
        self,
        body: QVBoxLayout,
        key: str,
        label: str,
        placeholder: str,
        add_tip: str,
        remove_tip: str,
    ) -> None:
        selector = _ModelSelector(
            label,
            placeholder,
            add_tip,
            remove_tip,
            scrollWidget=self._scrollWidget,
            parent=self,
        )
        selector.changed.connect(
            lambda options, current, key=key: self._on_model_options_changed(
                key, options, current
            )
        )
        selector.combo.setToolTip(
            PROFILE_PARAM_DESCRIPTIONS.get(key, label) or label
        )
        self._model_selectors[key] = selector
        self._model_rows[key] = selector
        body.addWidget(selector)

    def _build_actions(self) -> QWidget:
        actions = QWidget(self)
        actions.setObjectName("LLMProfileCardActions")
        layout = QHBoxLayout(actions)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)
        test_btn = QPushButton(self.tr("Test Connection"), actions)
        test_btn.setObjectName("ConfigButton")
        test_btn.clicked.connect(self._on_test_connection)
        fetch_btn = QPushButton(self.tr("Fetch Models"), actions)
        fetch_btn.setObjectName("ConfigButton")
        fetch_btn.clicked.connect(self._on_fetch_models)
        layout.addWidget(test_btn)
        layout.addWidget(fetch_btn)

        self._image_actions = QWidget(actions)
        image_layout = QHBoxLayout(self._image_actions)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(8)
        test_image_btn = QPushButton(self.tr("Test Image Endpoint"), self._image_actions)
        test_image_btn.setObjectName("ConfigButton")
        test_image_btn.clicked.connect(self._on_test_image_connection)
        fetch_image_btn = QPushButton(self.tr("Fetch Image Models"), self._image_actions)
        fetch_image_btn.setObjectName("ConfigButton")
        fetch_image_btn.clicked.connect(self._on_fetch_image_models)
        image_layout.addWidget(test_image_btn)
        image_layout.addWidget(fetch_image_btn)
        layout.addWidget(self._image_actions)
        layout.addStretch()
        return actions

    # ── 状态同步 ──

    def sync_from_profile(self) -> None:
        name = self.profile.get("name", "")
        self._name_label.setText(name)
        self._text_badge.set_active(True)
        self._vision_badge.set_active(bool(self.profile.get("vision_support", False)))
        self._image_badge.set_active(bool(self.profile.get("image_support", False)))
        self._sync_model_selectors()
        self._refresh_key_status()
        self._refresh_use_state()
        builtin = bool(self.profile.get("builtin", False))
        self._delete_btn.setEnabled(not builtin)
        if builtin:
            self._delete_btn.setToolTip(self.tr("Built-in profiles cannot be deleted."))
        self._refresh_conditional_visibility()

    def _refresh_use_state(self) -> None:
        """标记这张卡是否是当前激活的 profile（含「没显式设过」的推断结果）。"""
        name = self.profile.get("name", "")
        active = bool(name) and name == get_default_profile_name()
        self._use_btn.blockSignals(True)
        self._use_btn.setChecked(active)
        self._use_btn.blockSignals(False)
        self._use_btn.setText(self.tr("In use") if active else self.tr("Use"))

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded:
            self._ensure_details()
            self._refresh_conditional_visibility()
        self._expanded = expanded
        if self._details is not None:
            self._details.setVisible(expanded)
        self._actions.setVisible(expanded)
        self._edit_btn.setIcon(
            QIcon(
                themed_icon_path(
                    "edit_activate.svg" if expanded else "edit.svg"
                )
            )
        )

    def toggle_expanded(self) -> None:
        self.set_expanded(not self._expanded)

    def is_expanded(self) -> bool:
        return self._expanded

    def _ensure_details(self) -> None:
        if self._details is not None:
            return
        sections = {
            key: (
                PROFILE_SECTION_TITLES.get(key, key),
                _param_defs(defs, self.profile),
            )
            for key, defs in PROFILE_SECTION_PARAM_DEFS.items()
        }
        details = LLMProfileDetailsWidget(
            _param_defs(PROFILE_COMMON_PARAM_DEFS, self.profile),
            sections,
            connection_values={
                key: str(self.profile.get(key, "") or "")
                for key in CONNECTION_PARAM_KEYS
            },
            image_endpoint_visible=bool(self.profile.get("image_support", False)),
            scrollWidget=self._scrollWidget,
            parent=self,
        )
        details.paramwidget_edited.connect(self._on_detail_edited)
        self._details = details
        # 插在 actions 之前，保持 header / summary / details / actions 顺序。
        self._body_layout.insertWidget(
            self._body_layout.indexOf(self._actions), details
        )

    def _refresh_conditional_visibility(self) -> None:
        image_support = bool(self.profile.get("image_support", False))
        image_row = self._model_rows.get("image_model")
        if image_row is not None:
            image_row.setVisible(image_support)
        self._image_actions.setVisible(image_support)
        if self._details is None:
            return
        self._details.set_section_visible("text", True)
        self._details.set_section_visible(
            "vision", bool(self.profile.get("vision_support", False))
        )
        self._details.set_section_visible("image", image_support)

    # ── 编辑 ──

    def _on_detail_edited(self, param_key: str, content_dict: dict) -> None:
        self._apply_value(param_key, content_dict.get("content"))

    def _apply_value(self, key: str, content) -> None:
        if key == "reasoning_effort":
            content = "" if content == _REASONING_DEFAULT else content
        conv = PROFILE_FIELD_TYPES.get(key)
        if conv is bool:
            value = bool(content)
        elif conv in (int, float):
            raw = str(content or "").strip()
            fallback = _PARAM_DEFAULTS.get(key, conv())
            try:
                value = conv(raw) if raw else fallback
            except (TypeError, ValueError):
                value = fallback
        else:
            value = "" if content is None else str(content)
            if key in _STRIP_STRING_KEYS:
                value = value.strip()
        self.profile[key] = value
        if key == "api_host":
            self._refresh_summary_text()
        if key == "api_key":
            self._refresh_key_status()

    def _on_model_options_changed(
        self, key: str, options: List[str], current: str
    ) -> None:
        """摘要行下拉/增删后写回 profile 并落盘（离散操作，非逐键）。"""
        self.profile[MODEL_OPTION_FIELDS[key]] = [
            str(option) for option in options
        ]
        self.profile[key] = str(current or "").strip()
        self._sync_model_selectors()
        self.persist_requested.emit()

    def _sync_model_selectors(self) -> None:
        for key, options_key in MODEL_OPTION_FIELDS.items():
            selector = self._model_selectors.get(key)
            if selector is None:
                continue
            options = self.profile.get(options_key)
            selector.set_options(
                options if isinstance(options, list) else [],
                str(self.profile.get(key, "") or ""),
            )
        self._refresh_summary_text()

    def _refresh_summary_text(self) -> None:
        host = str(self.profile.get("api_host", "") or "")
        self._summary_label.setText(host)

    def _refresh_key_status(self) -> None:
        has_key = bool(str(self.profile.get("api_key", "") or "").strip())
        self._key_icon.setPixmap(
            render_svg_pixmap(
                themed_icon_path(
                    "llm_key_ok.svg" if has_key else "llm_key_missing.svg"
                ),
                16,
                16,
                self.devicePixelRatioF(),
            )
        )
        self._key_icon.setToolTip(
            self.tr("API key set") if has_key else self.tr("No API key")
        )

    def _toggle_vision_support(self) -> None:
        self.profile["vision_support"] = not bool(
            self.profile.get("vision_support", False)
        )
        self._vision_badge.set_active(bool(self.profile["vision_support"]))
        self._refresh_conditional_visibility()

    def _toggle_image_support(self) -> None:
        self.profile["image_support"] = not bool(
            self.profile.get("image_support", False)
        )
        self._image_badge.set_active(bool(self.profile["image_support"]))
        self._refresh_conditional_visibility()

    # ── 网络（Test / Fetch，走后台线程）──

    def set_modal_runner(self, runner) -> None:
        self._modal_runner = runner

    def _exec_dialog(self, dialog) -> int:
        if self._modal_runner is not None:
            return self._modal_runner(dialog)
        return dialog.exec()

    def _start_net(self, callback, on_ok, on_err) -> None:
        prev = self._net_worker
        if prev is not None and prev.isRunning():
            return
        worker = NetWorker(self, callback)
        worker.finished_ok.connect(on_ok)
        worker.finished_err.connect(on_err)
        self._net_worker = worker
        worker.start()

    def _net_error_message(self, error, host: str) -> str:
        if isinstance(error, httpx.HTTPStatusError):
            return self.tr("HTTP {code}: {text}").format(
                code=error.response.status_code, text=error.response.text[:200]
            )
        if isinstance(error, httpx.ConnectError):
            return self.tr(
                "Could not connect to {host}.\nPlease check the URL and your network."
            ).format(host=host)
        if isinstance(error, httpx.TimeoutException):
            return self.tr("Connection timed out. Check the URL and network.")
        return self.tr("Error: {err}").format(err=error)

    def _show_test_error(self, error, host: str) -> None:
        QMessageBox.warning(
            self, self.tr("Connection Failed"), self._net_error_message(error, host)
        )

    def _show_fetch_error(self, error, host: str) -> None:
        QMessageBox.warning(
            self,
            self.tr("Error"),
            self.tr("Failed to fetch model list: {err}").format(
                err=self._net_error_message(error, host)
            ),
        )

    def _test_success(self) -> None:
        QMessageBox.information(
            self,
            self.tr("Connection Successful"),
            self.tr("Connected! API is reachable and credentials are valid."),
        )

    def _pick_model(self, names: List[str], key: str) -> None:
        """Fetch 结果多选加入清单，并把首个选中项设为当前模型。"""
        if not names:
            QMessageBox.information(
                self, self.tr("Notice"), self.tr("No models found.")
            )
            return
        dialog = FilterableListDialog(
            self, self.tr("Select Models"), names, multi_select=True
        )
        if self._exec_dialog(dialog) != FilterableListDialog.DialogCode.Accepted:
            return
        picked = dialog.selected_items or ([dialog.selected] if dialog.selected else [])
        if not picked:
            return
        for name in picked:
            remember_model_option(self.profile, key, name)
        self.profile[key] = picked[0]
        self._sync_model_selectors()
        self.persist_requested.emit()

    def _on_test_connection(self) -> None:
        host = str(self.profile.get("api_host", "") or "").strip()
        key = str(self.profile.get("api_key", "") or "").strip()
        if not host:
            QMessageBox.warning(
                self, self.tr("Warning"), self.tr("Host is required.")
            )
            return
        if not key:
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("A valid API key is required to test the connection."),
            )
            return
        proxy = str(self.profile.get("proxy", "") or "")
        self._start_net(
            lambda: probe_connection(host, key, proxy),
            lambda _r: self._test_success(),
            lambda e: self._show_test_error(e, host),
        )

    def _on_fetch_models(self) -> None:
        host = str(self.profile.get("api_host", "") or "").strip()
        key = str(self.profile.get("api_key", "") or "").strip()
        if not host or not key:
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("Host and API key are required to fetch the model list."),
            )
            return
        proxy = str(self.profile.get("proxy", "") or "")
        self._start_net(
            lambda: probe_model_list(host, key, proxy),
            lambda names: self._pick_model(names, "model"),
            lambda e: self._show_fetch_error(e, host),
        )

    def _on_test_image_connection(self) -> None:
        base_url = str(self.profile.get("image_base_url", "") or "").strip()
        key = str(self.profile.get("api_key", "") or "").strip()
        if not base_url:
            QMessageBox.warning(
                self, self.tr("Warning"), self.tr("Image endpoint is required.")
            )
            return
        proxy = str(self.profile.get("proxy", "") or "")
        self._start_net(
            lambda: fetch_image_models(base_url, key, proxy=proxy),
            lambda _r: self._test_success(),
            lambda e: self._show_test_error(e, base_url),
        )

    def _on_fetch_image_models(self) -> None:
        base_url = str(self.profile.get("image_base_url", "") or "").strip()
        key = str(self.profile.get("api_key", "") or "").strip()
        if not base_url:
            QMessageBox.warning(
                self, self.tr("Warning"), self.tr("Image endpoint is required.")
            )
            return
        proxy = str(self.profile.get("proxy", "") or "")
        self._start_net(
            lambda: fetch_image_models(base_url, key, proxy=proxy),
            lambda names: self._pick_model(names, "image_model"),
            lambda e: self._show_fetch_error(e, base_url),
        )


class LLMProfileListWidget(QWidget):
    """LLM Profile 页：工具栏 + 卡片列表。

    保存时机与旧实现一致——只在增删 / 恢复内置 / Fetch 成功 / 离开页面时
    落盘并发出 ``profiles_changed``，避免每敲一个字就触发 module_manager
    重建 OCR / 翻译的参数面板。
    """

    profiles_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._profiles = load_profiles()
        self._loaded_raw = get_profiles_raw()
        self._cards: List[LLMProfileCardWidget] = []
        self._modal_runner = None
        self._build_ui()
        self._rebuild()

    def set_modal_runner(self, runner) -> None:
        self._modal_runner = runner
        for card in self._cards:
            card.set_modal_runner(runner)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QWidget(self)
        toolbar.setObjectName("LLMProfileToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(24, 12, 24, 8)
        toolbar_layout.setSpacing(8)
        add_btn = QPushButton(self.tr("+ Add"), toolbar)
        add_btn.setObjectName("ConfigButton")
        add_btn.clicked.connect(self._on_add)
        restore_btn = QPushButton(self.tr("Restore Built-ins"), toolbar)
        restore_btn.setObjectName("ConfigButton")
        restore_btn.clicked.connect(self._on_restore_builtins)
        toolbar_layout.addWidget(add_btn)
        toolbar_layout.addWidget(restore_btn)
        toolbar_layout.addStretch()
        layout.addWidget(toolbar)

        self._cards_layout = QVBoxLayout()
        self._cards_layout.setContentsMargins(24, 0, 24, 24)
        self._cards_layout.setSpacing(8)
        layout.addLayout(self._cards_layout)
        layout.addStretch()

    # ── 列表维护 ──

    def _rebuild(self) -> None:
        for card in self._cards:
            card.hide()
            card.setParent(None)
            card.deleteLater()
        self._cards = []
        for profile in self._profiles:
            card = LLMProfileCardWidget(profile, scrollWidget=self, parent=self)
            card.persist_requested.connect(self._persist)
            card.delete_requested.connect(self._on_delete_requested)
            card.use_requested.connect(self._on_use_requested)
            card.set_modal_runner(self._modal_runner)
            self._cards_layout.addWidget(card)
            self._cards.append(card)

    def _persist(self) -> None:
        save_all_profiles(self._profiles)
        self._loaded_raw = get_profiles_raw()
        self._sync_use_state()
        self.profiles_changed.emit()

    def _sync_use_state(self) -> None:
        for card in self._cards:
            card._refresh_use_state()

    def _on_use_requested(self, name: str) -> None:
        """激活某张卡：先落盘卡片上的编辑，再记激活项。

        可用性判断读的是已保存的 profile，所以必须先 ``save_all_profiles``
        ——否则刚填好的 key 还没生效就被判成不可用。
        """
        save_all_profiles(self._profiles)
        self._loaded_raw = get_profiles_raw()
        set_default_profile(name)
        self._sync_use_state()
        self.profiles_changed.emit()

    def _unique_name(self, base: str) -> str:
        existing = {p.get("name", "") for p in self._profiles}
        if base not in existing:
            return base
        index = 1
        while f"{base} ({index})" in existing:
            index += 1
        return f"{base} ({index})"

    def _on_add(self) -> None:
        new_profile = dict(SAMPLE_PROFILES[0])
        new_profile["builtin"] = False
        new_profile["name"] = self._unique_name(self.tr("New Profile"))
        self._profiles.append(new_profile)
        self._persist()
        self._rebuild()
        self.focus_profile(new_profile["name"])

    def _on_delete_requested(self, name: str) -> None:
        index = next(
            (
                i
                for i, p in enumerate(self._profiles)
                if p.get("name") == name
            ),
            None,
        )
        if index is None:
            return
        if self._profiles[index].get("builtin"):
            return
        reply = QMessageBox.question(
            self,
            self.tr("Confirm Delete"),
            self.tr('Delete profile "{name}"?').format(name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if get_default_profile_name() == name:
            # 激活项随删除失效：清空显式记录，让解析层回退到下一个可用项
            set_default_profile("")
        del self._profiles[index]
        self._persist()
        self._rebuild()

    def _on_restore_builtins(self) -> None:
        default_map = {
            p["name"]: dict(p) for p in SAMPLE_PROFILES if p.get("builtin")
        }
        existing_names = {p.get("name") for p in self._profiles}
        added = 0
        for name, defaults in default_map.items():
            if name not in existing_names:
                self._profiles.append(defaults)
                added += 1
        if added:
            self._persist()
            self._rebuild()
            QMessageBox.information(
                self,
                self.tr("Restored"),
                self.tr("Restored {n} built-in profile(s).").format(n=added),
            )
        else:
            QMessageBox.information(
                self,
                self.tr("No Change"),
                self.tr("All built-in profiles already exist."),
            )

    # ── 外部入口 ──

    def focus_profile(self, name: str) -> None:
        """展开并滚动到指定 profile 卡片（跨页跳转用）。"""
        for card in self._cards:
            if card.profile.get("name") == name:
                card.set_expanded(True)
                area = self._scroll_area()
                if area is not None:
                    area.ensureWidgetVisible(card)
                return

    def _scroll_area(self) -> Optional[QScrollArea]:
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    def hideEvent(self, event) -> None:
        """离开页面时落盘（与旧实现一致）。"""
        self._persist()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        """他处（如画布 AI 修图的模型栏）改过 profile 时重新载入，避免旧副本回写覆盖。"""
        if get_profiles_raw() != self._loaded_raw:
            self._profiles = load_profiles()
            self._loaded_raw = get_profiles_raw()
            self._rebuild()
        super().showEvent(event)
