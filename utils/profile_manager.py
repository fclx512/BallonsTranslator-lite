"""
Shared profile manager for LLM API configurations.

Profiles are stored as a JSON string in pcfg.module.model_profiles.
Both the translator and LLM OCR modules read from this shared pool.
"""

import json
from typing import List, Dict, Optional, Callable

from .config import pcfg, save_config
from .logger import logger as LOGGER

# ── Default values ──────────────────────────────────────────────────

DEFAULT_PROMPT_TEMPLATE = (
    "请将以下 {from_lang} 文本翻译为 {to_lang}：\n"
    "{input_json}"
)
DEFAULT_CHAT_SAMPLES = (
    "日本語-简体中文:\n"
    "    source:\n"
    "        - 二人のちゅーを 目撃した ぼっちちゃん\n"
    "        - 大好きなお友達には あいさつ代わりに ちゅーするんだって\n"
    "    target:\n"
    "        - 小孤独目击了两人的接吻\n"
    "        - 我听说人们会把亲吻作为与喜爱的朋友打招呼的方式"
)
DEFAULT_OCR_PROMPT = (
    "Perform OCR on the provided manga image snippet. The language is **{language}**.\n"
    "Recognize all text, including handwritten sound effects (SFX).\n"
    "**CRITICAL INSTRUCTION:** If you see jumbled characters, it is likely vertical text "
    "that was read horizontally. First, mentally reconstruct the correct vertical text.\n"
    "**OUTPUT FORMATTING:** All recognized text from the image must be consolidated "
    "into a **single, continuous horizontal line**. Do not use newlines.\n"
    "Your final output must be ONLY the recognized text. No explanations."
)
DEFAULT_OCR_SYSTEM_PROMPT = (
    "You are a specialized OCR engine for manga and comics. "
    "Your primary function is to accurately extract and consolidate all recognized text "
    "from an image into a **single, continuous horizontal line**. "
    "You must return only the raw, recognized text. "
    "You do not interpret, translate, or explain the content. "
    "You are designed to intelligently handle common OCR errors, such as "
    "reconstructing jumbled characters that result from misreading vertical text."
)

SAMPLE_PROFILES = [
    {
        "name": "OpenAI", "builtin": False, "vision_support": True,
        "api_host": "https://api.openai.com/v1", "api_key": "", "model": "gpt-4o",
        "temperature": 0.1, "top_p": 1.0, "max_tokens": "",
        "proxy": "", "requests_per_minute": 20, "delay": 0.3,
        "response_format": "json_object",
        "prompt_template": DEFAULT_PROMPT_TEMPLATE, "chat_samples": DEFAULT_CHAT_SAMPLES,
        "frequency_penalty": "", "presence_penalty": "",
        "ocr_prompt": DEFAULT_OCR_PROMPT, "ocr_system_prompt": DEFAULT_OCR_SYSTEM_PROMPT,
        "ocr_detail_level": "auto", "ocr_max_response_tokens": 4096,
    },
    {
        "name": "OpenRouter", "builtin": False, "vision_support": True,
        "api_host": "https://openrouter.ai/api/v1", "api_key": "", "model": "",
        "temperature": 0.1, "top_p": 1.0, "max_tokens": "",
        "proxy": "", "requests_per_minute": 20, "delay": 0.3,
        "response_format": "json_object",
        "prompt_template": DEFAULT_PROMPT_TEMPLATE, "chat_samples": DEFAULT_CHAT_SAMPLES,
        "frequency_penalty": "", "presence_penalty": "",
        "ocr_prompt": DEFAULT_OCR_PROMPT, "ocr_system_prompt": DEFAULT_OCR_SYSTEM_PROMPT,
        "ocr_detail_level": "auto", "ocr_max_response_tokens": 4096,
    },
    {
        "name": "DeepSeek", "builtin": True, "vision_support": False,
        "api_host": "https://api.deepseek.com/v1", "api_key": "", "model": "",
        "temperature": 0.1, "top_p": 1.0, "max_tokens": "",
        "proxy": "", "requests_per_minute": 20, "delay": 0.3,
        "response_format": "json_object",
        "prompt_template": DEFAULT_PROMPT_TEMPLATE, "chat_samples": DEFAULT_CHAT_SAMPLES,
        "frequency_penalty": "", "presence_penalty": "",
        "ocr_prompt": DEFAULT_OCR_PROMPT, "ocr_system_prompt": DEFAULT_OCR_SYSTEM_PROMPT,
        "ocr_detail_level": "auto", "ocr_max_response_tokens": 4096,
    },
    {
        "name": "LM Studio", "builtin": True, "vision_support": True,
        "api_host": "http://localhost:1234/v1", "api_key": "dummy-key", "model": "",
        "temperature": 0.1, "top_p": 1.0, "max_tokens": "",
        "proxy": "", "requests_per_minute": 20, "delay": 0.3,
        "response_format": "json_object",
        "prompt_template": DEFAULT_PROMPT_TEMPLATE, "chat_samples": DEFAULT_CHAT_SAMPLES,
        "frequency_penalty": "", "presence_penalty": "",
        "ocr_prompt": DEFAULT_OCR_PROMPT, "ocr_system_prompt": DEFAULT_OCR_SYSTEM_PROMPT,
        "ocr_detail_level": "auto", "ocr_max_response_tokens": 4096,
    },
    {
        "name": "Ollama", "builtin": True, "vision_support": True,
        "api_host": "http://localhost:11434/v1", "api_key": "dummy-key", "model": "",
        "temperature": 0.1, "top_p": 1.0, "max_tokens": "",
        "proxy": "", "requests_per_minute": 20, "delay": 0.3,
        "response_format": "json_object",
        "prompt_template": DEFAULT_PROMPT_TEMPLATE, "chat_samples": DEFAULT_CHAT_SAMPLES,
        "frequency_penalty": "", "presence_penalty": "",
        "ocr_prompt": DEFAULT_OCR_PROMPT, "ocr_system_prompt": DEFAULT_OCR_SYSTEM_PROMPT,
        "ocr_detail_level": "auto", "ocr_max_response_tokens": 4096,
    },
]


# ── Profile Manager ─────────────────────────────────────────────────

PROFILE_FIELDS = [
    "name", "builtin", "vision_support",
    "api_host", "api_key", "model",
    "temperature", "top_p", "max_tokens",
    "proxy", "requests_per_minute", "delay",
    "response_format",
    "prompt_template", "chat_samples",
    "frequency_penalty", "presence_penalty",
    "ocr_prompt", "ocr_system_prompt", "ocr_detail_level", "ocr_max_response_tokens",
]


def migrate_old_profiles():
    """Migrate profiles from translator's _profiles_storage to model_profiles."""
    if pcfg.module.model_profiles:
        return  # Already have profiles in the new location
    old_storage = pcfg.module.translator_params.get("LLM_API_Translator", {})
    raw = old_storage.get("_profiles_storage", "")
    if isinstance(raw, dict):
        raw = raw.get("value", "")
    if not raw:
        return
    try:
        old_profiles = json.loads(raw)
        if not isinstance(old_profiles, list):
            return
        # Convert old format to new: add missing fields
        migrated = []
        for p in old_profiles:
            entry = dict(SAMPLE_PROFILES[0])  # start with defaults
            entry.update(p)  # overlay saved values
            # Default vision_support to False for migrated profiles — user must opt in
            entry["vision_support"] = p.get("vision_support", False)
            # Ensure OCR fields exist with defaults
            entry.setdefault("ocr_prompt", DEFAULT_OCR_PROMPT)
            entry.setdefault("ocr_system_prompt", DEFAULT_OCR_SYSTEM_PROMPT)
            entry.setdefault("ocr_detail_level", "auto")
            entry.setdefault("ocr_max_response_tokens", 4096)
            entry.setdefault("proxy", "")
            entry.setdefault("requests_per_minute", 20)
            entry.setdefault("delay", 0.3)
            migrated.append(entry)
        pcfg.module.model_profiles = json.dumps(migrated, ensure_ascii=False)
        # Clean up old storage
        old_storage.pop("_profiles_storage", None)
        LOGGER.info(f"Migrated {len(migrated)} profiles from translator storage to model_profiles.")
    except (json.JSONDecodeError, TypeError, Exception) as e:
        LOGGER.warning(f"Failed to migrate old profiles: {e}")


def get_profiles_raw() -> str:
    return pcfg.module.model_profiles or ""


def set_profiles_raw(raw: str):
    pcfg.module.model_profiles = raw


def load_profiles() -> List[Dict]:
    """Deserialize profiles from config. Falls back to SAMPLE_PROFILES if empty."""
    raw = get_profiles_raw()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                _merge_builtin_defaults(data)
                return data
        except (json.JSONDecodeError, TypeError):
            pass
    profiles = [dict(p) for p in SAMPLE_PROFILES]
    _save_profiles(profiles)
    return profiles


def _merge_builtin_defaults(profiles: List[Dict]):
    """Ensure all SAMPLE_PROFILES builtins exist with current default fields."""
    default_map = {p["name"]: p for p in SAMPLE_PROFILES if p.get("builtin")}
    existing_names = {p.get("name") for p in profiles}
    for name, defaults in default_map.items():
        if name not in existing_names:
            profiles.append(dict(defaults))
    for profile in profiles:
        if profile.get("builtin") and profile["name"] in default_map:
            defaults = default_map[profile["name"]]
            for key, val in defaults.items():
                if key not in profile:
                    profile[key] = val


def _save_profiles(profiles: List[Dict]):
    set_profiles_raw(json.dumps(profiles, ensure_ascii=False))


def get_profile_names() -> List[str]:
    return [p.get("name", "") for p in load_profiles() if p.get("name")]


def find_profile(name: str) -> Optional[Dict]:
    for p in load_profiles():
        if p.get("name") == name:
            return p
    return None


def get_vision_profiles() -> List[Dict]:
    """Return profiles that have vision_support enabled."""
    return [p for p in load_profiles() if p.get("vision_support", False)]


def get_vision_profile_names() -> List[str]:
    return [p.get("name", "") for p in get_vision_profiles() if p.get("name")]


def save_profile(name: str, data: Dict):
    """Update or add a profile, then persist to disk."""
    profiles = load_profiles()
    for i, p in enumerate(profiles):
        if p.get("name") == name:
            profiles[i] = data
            break
    else:
        profiles.append(data)
    _save_profiles(profiles)


def delete_profile(name: str):
    """Remove a profile by name. Builtin profiles cannot be deleted."""
    profiles = load_profiles()
    for i, p in enumerate(profiles):
        if p.get("name") == name:
            if p.get("builtin"):
                return False
            del profiles[i]
            break
    _save_profiles(profiles)
    return True


def save_all_profiles(profiles: List[Dict]):
    """Replace all profiles and persist to disk immediately."""
    _save_profiles(profiles)
    save_config()


# ── Dialog ──────────────────────────────────────────────────────────

try:
    from PyQt6.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QListWidget,
        QLineEdit, QPushButton, QLabel, QFormLayout,
        QWidget, QSplitter, QMessageBox,
        QComboBox, QInputDialog, QTextEdit, QScrollArea, QCheckBox,
        QSpinBox, QDoubleSpinBox,
    )
except ImportError:
    from PyQt5.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QListWidget,
        QLineEdit, QPushButton, QLabel, QFormLayout,
        QWidget, QSplitter, QMessageBox,
        QComboBox, QInputDialog, QTextEdit, QScrollArea, QCheckBox,
        QSpinBox, QDoubleSpinBox,
    )

import httpx


class ProfileManagerDialog(QDialog):
    """Unified dialog to manage API profiles for both translation and OCR."""

    def __init__(self, parent, profiles_data: List[Dict], on_changed=None):
        super().__init__(parent)
        self._profiles = profiles_data
        self._on_changed = on_changed
        self._current_row = -1
        self.setWindowTitle(self.tr("Manage API Profiles"))
        self.setMinimumSize(720, 540)
        self._build_ui()

    def _is_builtin(self, row: int) -> bool:
        return 0 <= row < len(self._profiles) and self._profiles[row].get("builtin", False)

    def _save_current_form(self):
        row = self._current_row
        if row < 0 or row >= len(self._profiles):
            return
        p = self._profiles[row]
        name = self.name_edit.text().strip()
        if not name:
            return
        p["name"] = name
        p["vision_support"] = self.vision_check.isChecked()
        p["api_host"] = self.host_edit.text().strip()
        p["api_key"] = self.key_edit.text().strip()
        p["model"] = self.model_edit.text().strip()
        p["proxy"] = self.proxy_edit.text().strip()
        try:
            p["requests_per_minute"] = int(self.rpm_spin.value())
        except (ValueError, TypeError):
            p["requests_per_minute"] = 20
        try:
            p["delay"] = float(self.delay_spin.value())
        except (ValueError, TypeError):
            p["delay"] = 0.3
        p["response_format"] = self.rf_combo.currentText()
        p["prompt_template"] = self.prompt_template_edit.toPlainText().strip()
        p["chat_samples"] = self.chat_samples_edit.toPlainText().strip()
        try:
            p["temperature"] = float(self.temp_edit.text() or "0.1")
        except ValueError:
            p["temperature"] = 0.1
        try:
            p["top_p"] = float(self.topp_edit.text() or "1.0")
        except ValueError:
            p["top_p"] = 1.0
        p["max_tokens"] = self.maxtok_edit.text().strip()
        for key in ["frequency_penalty", "presence_penalty"]:
            edit = self.fp_edit if key == "frequency_penalty" else self.pp_edit
            val = edit.text().strip()
            if val:
                try:
                    p[key] = float(val)
                except ValueError:
                    pass
            elif key in p:
                del p[key]
        # OCR-specific fields
        p["ocr_prompt"] = self.ocr_prompt_edit.toPlainText().strip()
        p["ocr_system_prompt"] = self.ocr_sysprompt_edit.toPlainText().strip()
        p["ocr_detail_level"] = self.ocr_detail_combo.currentText()
        try:
            p["ocr_max_response_tokens"] = int(self.ocr_maxtok_spin.value())
        except (ValueError, TypeError):
            p["ocr_max_response_tokens"] = 4096

        if self._on_changed:
            self._on_changed()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        splitter = QSplitter(self)

        # ── Left: profile list ──
        left_widget = QWidget()
        left_widget.setFixedWidth(220)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(2, 2, 2, 2)
        left_layout.addWidget(QLabel(self.tr("Saved Profiles:")))
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_select)
        left_layout.addWidget(self.list_widget, 1)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        self.add_new_btn = QPushButton(self.tr("+ Add"))
        self.add_new_btn.clicked.connect(self._on_add_new)
        self.delete_btn = QPushButton(self.tr("Delete"))
        self.delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self.add_new_btn, 1)
        btn_row.addWidget(self.delete_btn, 1)
        left_layout.addLayout(btn_row)

        # ── Right: scrollable edit form ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(5, 5, 5, 5)

        # Basic Settings
        right_layout.addWidget(QLabel(self.tr("Basic Settings:")))
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(self.tr("e.g., My Custom API"))
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("https://api.example.com/v1")
        self.key_edit = QLineEdit()
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("gpt-4o, ...")
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_edit)
        fetch_btn = QPushButton(self.tr("Fetch Models"))
        fetch_btn.clicked.connect(self._on_fetch_models)
        model_row.addWidget(fetch_btn)
        self.vision_check = QCheckBox(self.tr("Vision support (for OCR)"))
        self.vision_check.setToolTip(self.tr(
            "Enable this for models that can process images. "
            "Vision-capable profiles will appear in the OCR model selector."
        ))
        self.temp_edit = QLineEdit()
        self.temp_edit.setPlaceholderText("0.1")
        self.topp_edit = QLineEdit()
        self.topp_edit.setPlaceholderText("1.0")
        self.maxtok_edit = QLineEdit()
        self.maxtok_edit.setPlaceholderText(self.tr("Unlimited (leave empty)"))
        form.addRow(self.tr("Name:"), self.name_edit)
        form.addRow(self.tr("Host:"), self.host_edit)
        form.addRow(self.tr("API Key:"), self.key_edit)
        form.addRow(self.tr("Model:"), model_row)
        form.addRow("", self.vision_check)
        form.addRow(self.tr("Temperature:"), self.temp_edit)
        form.addRow(self.tr("Top P:"), self.topp_edit)
        form.addRow(self.tr("Max Tokens:"), self.maxtok_edit)
        right_layout.addLayout(form)

        # Connection & Rate Limiting
        right_layout.addWidget(QLabel(self.tr("Connection & Rate Limiting:")))
        conn_form = QFormLayout()
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("http://user:pass@host:port")
        self.rpm_spin = QSpinBox()
        self.rpm_spin.setRange(0, 10000)
        self.rpm_spin.setValue(20)
        self.rpm_spin.setToolTip(self.tr("0 = unlimited"))
        self.delay_spin = QDoubleSpinBox()
        self.delay_spin.setRange(0, 60)
        self.delay_spin.setSingleStep(0.1)
        self.delay_spin.setDecimals(2)
        self.delay_spin.setValue(0.3)
        conn_form.addRow(self.tr("Proxy:"), self.proxy_edit)
        conn_form.addRow(self.tr("Requests/min:"), self.rpm_spin)
        conn_form.addRow(self.tr("Delay (s):"), self.delay_spin)
        right_layout.addLayout(conn_form)

        # Advanced (optional) — Translation settings
        right_layout.addWidget(QLabel(self.tr("Translation Settings (optional):")))
        adv_form = QFormLayout()
        self.rf_combo = QComboBox()
        self.rf_combo.addItems(["json_object", "json_schema"])
        self.rf_combo.setCurrentText("json_object")
        self.prompt_template_edit = QTextEdit()
        self.prompt_template_edit.setPlaceholderText(
            self.tr("Translate to {to_lang}:\n{input_json}")
        )
        self.prompt_template_edit.setMinimumHeight(80)
        self.chat_samples_edit = QTextEdit()
        self.chat_samples_edit.setPlaceholderText(
            self.tr("{to_lang}-{from_lang}:\n    source:\n        - text1\n    target:\n        - trans1")
        )
        self.chat_samples_edit.setMinimumHeight(80)
        self.fp_edit = QLineEdit()
        self.fp_edit.setPlaceholderText("0.0")
        self.pp_edit = QLineEdit()
        self.pp_edit.setPlaceholderText("0.0")
        adv_form.addRow(self.tr("Response Format:"), self.rf_combo)
        adv_form.addRow(self.tr("Prompt Template:"), self.prompt_template_edit)
        adv_form.addRow(self.tr("Few-Shot Examples:"), self.chat_samples_edit)
        adv_form.addRow(self.tr("Frequency Penalty:"), self.fp_edit)
        adv_form.addRow(self.tr("Presence Penalty:"), self.pp_edit)
        right_layout.addLayout(adv_form)

        # OCR Settings
        right_layout.addWidget(QLabel(self.tr("OCR Settings (optional):")))
        ocr_form = QFormLayout()
        self.ocr_prompt_edit = QTextEdit()
        self.ocr_prompt_edit.setPlaceholderText(
            self.tr("OCR prompt with {language} placeholder.")
        )
        self.ocr_prompt_edit.setMinimumHeight(80)
        self.ocr_sysprompt_edit = QTextEdit()
        self.ocr_sysprompt_edit.setPlaceholderText(
            self.tr("Optional system prompt for OCR.")
        )
        self.ocr_sysprompt_edit.setMinimumHeight(60)
        self.ocr_detail_combo = QComboBox()
        self.ocr_detail_combo.addItems(["auto", "low", "high"])
        self.ocr_detail_combo.setCurrentText("auto")
        self.ocr_maxtok_spin = QSpinBox()
        self.ocr_maxtok_spin.setRange(64, 131072)
        self.ocr_maxtok_spin.setValue(4096)
        ocr_form.addRow(self.tr("OCR Prompt:"), self.ocr_prompt_edit)
        ocr_form.addRow(self.tr("OCR System Prompt:"), self.ocr_sysprompt_edit)
        ocr_form.addRow(self.tr("Detail Level:"), self.ocr_detail_combo)
        ocr_form.addRow(self.tr("Max Tokens:"), self.ocr_maxtok_spin)
        right_layout.addLayout(ocr_form)

        right_layout.addStretch()

        scroll.setWidget(right_widget)
        splitter.addWidget(left_widget)
        splitter.addWidget(scroll)
        splitter.setSizes([200, 520])
        layout.addWidget(splitter)

        self._refresh_list()
        self._clear_fields()
        self._update_delete_button()

    def _refresh_list(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for p in self._profiles:
            label = p.get("name", "")
            if p.get("builtin"):
                label += self.tr(" (built-in)")
            self.list_widget.addItem(label)
        self.list_widget.blockSignals(False)

    def _clear_fields(self):
        for edit in [self.name_edit, self.host_edit, self.key_edit, self.model_edit,
                     self.temp_edit, self.topp_edit, self.maxtok_edit,
                     self.fp_edit, self.pp_edit, self.prompt_template_edit,
                     self.proxy_edit, self.ocr_prompt_edit, self.ocr_sysprompt_edit]:
            edit.clear()
        self.chat_samples_edit.clear()
        self.chat_samples_edit.setPlainText("")
        self.vision_check.setChecked(False)
        self.rf_combo.setCurrentText("json_object")
        self.ocr_detail_combo.setCurrentText("auto")
        self.rpm_spin.setValue(20)
        self.delay_spin.setValue(0.3)
        self.ocr_maxtok_spin.setValue(4096)

    def _update_delete_button(self):
        row = self.list_widget.currentRow()
        self.delete_btn.setEnabled(not self._is_builtin(row))

    def _on_add_new(self):
        self._save_current_form()
        self.list_widget.clearSelection()
        self._current_row = -1
        self._clear_fields()
        self.name_edit.setFocus()
        self.delete_btn.setEnabled(False)
        new_profile = {"name": self.tr("New Profile")}
        self._profiles.append(new_profile)
        if self._on_changed:
            self._on_changed()
        self._refresh_list()
        self.list_widget.setCurrentRow(len(self._profiles) - 1)

    def _on_select(self, row: int):
        if row < 0 or row >= len(self._profiles):
            return
        self._save_current_form()
        self._current_row = row
        p = self._profiles[row]
        self.name_edit.setText(p.get("name", ""))
        self.vision_check.setChecked(p.get("vision_support", False))
        self.host_edit.setText(p.get("api_host", ""))
        self.key_edit.setText(p.get("api_key", ""))
        self.model_edit.setText(p.get("model", ""))
        self.proxy_edit.setText(p.get("proxy", ""))
        try:
            self.rpm_spin.setValue(int(p.get("requests_per_minute", 20)))
        except (ValueError, TypeError):
            self.rpm_spin.setValue(20)
        try:
            self.delay_spin.setValue(float(p.get("delay", 0.3)))
        except (ValueError, TypeError):
            self.delay_spin.setValue(0.3)
        self.temp_edit.setText(str(p.get("temperature", "0.1")))
        self.topp_edit.setText(str(p.get("top_p", "1.0")))
        self.maxtok_edit.setText(str(p.get("max_tokens", "")))
        self.rf_combo.setCurrentText(p.get("response_format", "json_object"))
        self.prompt_template_edit.setPlainText(p.get("prompt_template", ""))
        self.chat_samples_edit.setPlainText(p.get("chat_samples", ""))
        self.fp_edit.setText(str(p.get("frequency_penalty", "")))
        self.pp_edit.setText(str(p.get("presence_penalty", "")))
        # OCR fields
        self.ocr_prompt_edit.setPlainText(p.get("ocr_prompt", ""))
        self.ocr_sysprompt_edit.setPlainText(p.get("ocr_system_prompt", ""))
        self.ocr_detail_combo.setCurrentText(p.get("ocr_detail_level", "auto"))
        try:
            self.ocr_maxtok_spin.setValue(int(p.get("ocr_max_response_tokens", 4096)))
        except (ValueError, TypeError):
            self.ocr_maxtok_spin.setValue(4096)
        self._update_delete_button()

    def _on_fetch_models(self):
        host = self.host_edit.text().strip()
        key = self.key_edit.text().strip()
        if not host or not key:
            QMessageBox.warning(self, self.tr("Warning"),
                self.tr("Host and API key are required to fetch the model list."))
            return
        try:
            with httpx.Client() as client:
                resp = client.get(
                    f"{host.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    models = resp.json().get("data", [])
                    names = sorted(m["id"] for m in models)
                    if not names:
                        QMessageBox.information(self, self.tr("Notice"), self.tr("No models found."))
                        return
                    name, ok = QInputDialog.getItem(
                        self, self.tr("Select Model"), self.tr("Choose a model:"), names, 0, False
                    )
                    if ok and name:
                        self.model_edit.setText(name)
                else:
                    QMessageBox.warning(
                        self, self.tr("Error"),
                        self.tr("Failed to fetch model list. HTTP {code}").format(code=resp.status_code)
                    )
        except Exception as e:
            QMessageBox.warning(self, self.tr("Error"), self.tr("Failed to fetch model list: {err}").format(err=e))

    def _on_delete(self):
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self._profiles) or self._is_builtin(row):
            return
        name = self._profiles[row].get("name", "")
        reply = QMessageBox.question(
            self, self.tr("Confirm Delete"),
            self.tr('Delete profile "{name}"?').format(name=name),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        del self._profiles[row]
        if self._on_changed:
            self._on_changed()
        self._current_row = -1
        self._refresh_list()
        self._clear_fields()
        self.delete_btn.setEnabled(False)

    def closeEvent(self, event):
        self._save_current_form()
        super().closeEvent(event)
