"""
Shared profile manager for LLM API configurations.

Profiles are stored as a JSON string in pcfg.module.model_profiles.
Both the translator and LLM OCR modules read from this shared pool.
"""

import json
from typing import Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import httpx
from qtpy.QtCore import Qt, QThread, Signal
from qtpy.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ui.custom_widget import (
    ConfigCheckBox,
    ConfigComboBox,
    ConfigLineEdit,
    ConfigSectionHeader,
    ConfigTextEdit,
    NoArrowsDoubleSpinBox,
    NoArrowsSpinBox,
)

from .config import pcfg, save_config
from .logger import logger as LOGGER

# ── Default values ──────────────────────────────────────────────────

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
DEFAULT_INPAINT_PROMPT = (
    "Clean up this comic or manga image for further scanlation. Remove all visible text "
    "elements, including speech bubble lettering, captions, sound effects, signs, labels "
    "and text-like watermarks. When text is removed, reconstruct the artwork that was "
    "hidden behind it by sampling the surrounding pixels and extending them into the gap "
    "so it looks as if nothing was ever there: continue the screentone, hatching, "
    "gradients, shading, panel borders and structural lines (speed lines, scan lines, "
    "cross-hatching) with consistent spacing, angle, density, colour and brightness. Do "
    "not leave a flat solid colour, a blank white gap, or a coloured smear where the text "
    "was. Keep all other non-text artwork intact: characters, faces, line art, "
    "backgrounds, speech bubbles, panel borders, lighting, colours, texture and "
    "composition. Do not translate, redraw with new text, add captions, or explain the "
    "edit. Return only the cleaned image."
)

SAMPLE_PROFILES = [
    {
        "name": "OpenAI",
        "builtin": False,
        "vision_support": True,
        "api_host": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o",
        "temperature": 0.1,
        "top_p": 1.0,
        "max_tokens": "",
        "proxy": "",
        "requests_per_minute": 20,
        "delay": 0.3,
        "reasoning_effort": "",
        "return_json_schema": False,
        "system_prompt": "",
        "ocr_prompt": DEFAULT_OCR_PROMPT,
        "ocr_system_prompt": DEFAULT_OCR_SYSTEM_PROMPT,
        "ocr_detail_level": "auto",
        "ocr_max_response_tokens": 4096,
        "image_support": False,
        "image_base_url": "",
        "image_model": "",
        "image_prompt": DEFAULT_INPAINT_PROMPT,
    },
    {
        "name": "OpenRouter",
        "builtin": False,
        "vision_support": True,
        "api_host": "https://openrouter.ai/api/v1",
        "api_key": "",
        "model": "",
        "temperature": 0.1,
        "top_p": 1.0,
        "max_tokens": "",
        "proxy": "",
        "requests_per_minute": 20,
        "delay": 0.3,
        "reasoning_effort": "",
        "return_json_schema": False,
        "system_prompt": "",
        "ocr_prompt": DEFAULT_OCR_PROMPT,
        "ocr_system_prompt": DEFAULT_OCR_SYSTEM_PROMPT,
        "ocr_detail_level": "auto",
        "ocr_max_response_tokens": 4096,
        "image_support": False,
        "image_base_url": "",
        "image_model": "",
        "image_prompt": DEFAULT_INPAINT_PROMPT,
    },
    {
        "name": "DeepSeek",
        "builtin": True,
        "vision_support": False,
        "api_host": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "",
        "temperature": 0.1,
        "top_p": 1.0,
        "max_tokens": "",
        "proxy": "",
        "requests_per_minute": 20,
        "delay": 0.3,
        "reasoning_effort": "",
        "return_json_schema": False,
        "system_prompt": "",
        "ocr_prompt": DEFAULT_OCR_PROMPT,
        "ocr_system_prompt": DEFAULT_OCR_SYSTEM_PROMPT,
        "ocr_detail_level": "auto",
        "ocr_max_response_tokens": 4096,
        "image_support": False,
        "image_base_url": "",
        "image_model": "",
        "image_prompt": DEFAULT_INPAINT_PROMPT,
    },
    {
        "name": "LM Studio",
        "builtin": True,
        "vision_support": True,
        "api_host": "http://localhost:1234/v1",
        "api_key": "dummy-key",
        "model": "",
        "temperature": 0.1,
        "top_p": 1.0,
        "max_tokens": "",
        "proxy": "",
        "requests_per_minute": 20,
        "delay": 0.3,
        "reasoning_effort": "",
        "return_json_schema": False,
        "system_prompt": "",
        "ocr_prompt": DEFAULT_OCR_PROMPT,
        "ocr_system_prompt": DEFAULT_OCR_SYSTEM_PROMPT,
        "ocr_detail_level": "auto",
        "ocr_max_response_tokens": 4096,
        "image_support": False,
        "image_base_url": "",
        "image_model": "",
        "image_prompt": DEFAULT_INPAINT_PROMPT,
    },
    {
        "name": "Ollama",
        "builtin": True,
        "vision_support": True,
        "api_host": "http://localhost:11434/v1",
        "api_key": "dummy-key",
        "model": "",
        "temperature": 0.1,
        "top_p": 1.0,
        "max_tokens": "",
        "proxy": "",
        "requests_per_minute": 20,
        "delay": 0.3,
        "reasoning_effort": "",
        "return_json_schema": False,
        "system_prompt": "",
        "ocr_prompt": DEFAULT_OCR_PROMPT,
        "ocr_system_prompt": DEFAULT_OCR_SYSTEM_PROMPT,
        "ocr_detail_level": "auto",
        "ocr_max_response_tokens": 4096,
        "image_support": False,
        "image_base_url": "",
        "image_model": "",
        "image_prompt": DEFAULT_INPAINT_PROMPT,
    },
]


# ── Profile Manager ─────────────────────────────────────────────────

PROFILE_FIELDS = [
    "name",
    "builtin",
    "vision_support",
    "api_host",
    "api_key",
    "model",
    "temperature",
    "top_p",
    "max_tokens",
    "proxy",
    "requests_per_minute",
    "delay",
    "reasoning_effort",
    "return_json_schema",
    "system_prompt",
    "ocr_prompt",
    "ocr_system_prompt",
    "ocr_detail_level",
    "ocr_max_response_tokens",
    "image_support",
    "image_base_url",
    "image_model",
    "image_prompt",
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
        LOGGER.info(
            f"Migrated {len(migrated)} profiles from translator storage to model_profiles."
        )
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


def get_image_profiles() -> List[Dict]:
    """Return profiles that have image inpainting enabled.

    Inpainting is gated by the image_support flag on each profile (managed in
    Model Management), so a profile that only does text/OCR does not appear in
    the online inpainter's profile selector.
    """
    return [p for p in load_profiles() if p.get("image_support", False)]


def get_image_profile_names() -> List[str]:
    return [p.get("name", "") for p in get_image_profiles() if p.get("name")]


# ── Image endpoint helpers (Test / Fetch Models for image inpainting) ──

def _is_gemini_host(base_url: str) -> bool:
    return urlparse(base_url).netloc.lower() == "generativelanguage.googleapis.com"


def _is_openrouter_host(base_url: str) -> bool:
    host = urlparse(base_url).netloc.lower()
    return host == "openrouter.ai" or host.endswith(".openrouter.ai")


def _join_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    endpoint = "/" + path.strip("/")
    return f"{base}{endpoint}"


def _image_models_url(image_base_url: str) -> str:
    """Derive the provider's model-list endpoint from an image endpoint.

    Gemini exposes ``/models`` under its versioned root; a pasted
    ``:generateContent`` or ``/models/<model>`` tail is stripped back to it.
    OpenAI-compatible / OpenRouter image endpoints usually sit at
    ``<api_root>/images/<action>``, so the list lives at ``<api_root>/models``.
    """
    base = image_base_url.rstrip("/")
    if _is_gemini_host(base):
        parsed = urlparse(base)
        path = parsed.path.rstrip("/")
        if ":generateContent" in path:
            path = path.split(":generateContent")[0].rstrip("/")
        if "/models" in path:
            path = path.split("/models")[0].rstrip("/")
        root = urlunparse(
            parsed._replace(path=path, params="", query="", fragment="")
        ).rstrip("/")
        return _join_url(root, "/models")
    fallback = base
    path = urlparse(base).path.rstrip("/")
    for action in ("/images/edits", "/images"):
        if path.endswith(action):
            fallback = base[: len(base) - len(action)]
            break
    return _join_url(fallback, "/models")


def _image_headers(base_url: str, api_key: str) -> dict:
    if _is_gemini_host(base_url):
        return {"x-goog-api-key": api_key or "", "Content-Type": "application/json"}
    return {"Authorization": f"Bearer {api_key or ''}"}


def _parse_image_models(payload, base_url: str) -> List[str]:
    if _is_gemini_host(base_url):
        models = payload.get("models", []) or []
        names = [m.get("name", "") if isinstance(m, dict) else "" for m in models]
        return [n.rsplit("/", 1)[-1] for n in names if n]
    data = payload.get("data", []) or []
    return [m.get("id", "") for m in data if isinstance(m, dict) and m.get("id")]


def fetch_image_models(
    image_base_url: str, api_key: str = "", proxy: str = "", timeout: float = 10
) -> List[str]:
    """Fetch the provider's model list for the given image endpoint.

    Raises the underlying ``httpx`` errors on failure, so callers can mirror the
    existing text-profile Test / Fetch Models UX.
    """
    if not image_base_url:
        raise ValueError("Image endpoint is required.")
    url = _image_models_url(image_base_url)
    client_kwargs = {
        "timeout": timeout,
        "headers": _image_headers(image_base_url, api_key),
    }
    if proxy:
        client_kwargs["proxy"] = proxy
    with httpx.Client(**client_kwargs) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return _parse_image_models(resp.json(), image_base_url)


def _probe_connection(host: str, api_key: str, proxy: str = "") -> None:
    """GET ``<host>/models`` and raise on any failure (used by the Test button).

    Runs on a background thread; raises raw ``httpx`` exceptions so callers map
    them to a friendly message without freezing the GUI.
    """
    client_kwargs = {"timeout": 10}
    if proxy:
        client_kwargs["proxy"] = proxy
    with httpx.Client(**client_kwargs) as client:
        resp = client.get(
            f"{host.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()


def _probe_model_list(host: str, api_key: str, proxy: str = "") -> List[str]:
    """GET ``<host>/models`` and return the sorted model ids (Fetch Models)."""
    client_kwargs = {"timeout": 10}
    if proxy:
        client_kwargs["proxy"] = proxy
    with httpx.Client(**client_kwargs) as client:
        resp = client.get(
            f"{host.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        models = resp.json().get("data", [])
        return sorted(m["id"] for m in models)


class _NetWorker(QThread):
    """Run a blocking network call off the GUI thread.

    The predecessor ran the request synchronously on the GUI thread, so a slow
    or unreachable host froze the whole window (Windows reports "Not
    Responding"). ``finished_ok`` / ``finished_err`` are delivered back on the
    GUI thread via a queued connection, keeping the UI responsive.
    """
    finished_ok = Signal(object)
    finished_err = Signal(object)

    def __init__(self, parent, callback):
        super().__init__(parent)
        self._callback = callback

    def run(self):
        try:
            result = self._callback()
        except Exception as e:  # noqa: BLE001 — deliberate: forward to GUI
            self.finished_err.emit(e)
        else:
            self.finished_ok.emit(result)


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


# ── Filterable List Dialog ──


class FilterableListDialog(QDialog):
    """Dialog with search bar + scrollable list. Returns selected item text."""

    def __init__(self, parent, title: str, items: List[str]):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(420, 500)
        self.selected = ""
        self._all_items = list(items)

        layout = QVBoxLayout(self)

        self.search_edit = ConfigLineEdit()
        self.search_edit.setPlaceholderText(self.tr("Search..."))
        self.search_edit.textChanged.connect(self._filter)
        layout.addWidget(self.search_edit)

        self.list_widget = QListWidget()
        self.list_widget.addItems(self._all_items)
        self.list_widget.itemDoubleClicked.connect(self._accept_selection)
        layout.addWidget(self.list_widget, 1)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton(self.tr("OK"))
        ok_btn.clicked.connect(self._accept_selection)
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        # Focus search bar so user can type immediately
        self.search_edit.setFocus()

    def _filter(self, text: str):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if not text:
                item.setHidden(False)
            else:
                item.setHidden(text.lower() not in item.text().lower())

    def _accept_selection(self):
        selected = self.list_widget.selectedItems()
        if selected:
            self.selected = selected[0].text()
        self.accept()


# ── Dialog ──


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
        return 0 <= row < len(self._profiles) and self._profiles[row].get(
            "builtin", False
        )

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
        p["reasoning_effort"] = self.reasoning_combo.currentData() or ""
        p["return_json_schema"] = self.return_json_schema_check.isChecked()
        p["system_prompt"] = self.system_prompt_edit.toPlainText().strip()
        try:
            p["temperature"] = float(self.temp_edit.text() or "0.1")
        except ValueError:
            p["temperature"] = 0.1
        try:
            p["top_p"] = float(self.topp_edit.text() or "1.0")
        except ValueError:
            p["top_p"] = 1.0
        p["max_tokens"] = self.maxtok_edit.text().strip()
        # OCR-specific fields
        p["ocr_prompt"] = self.ocr_prompt_edit.toPlainText().strip()
        p["ocr_system_prompt"] = self.ocr_sysprompt_edit.toPlainText().strip()
        p["ocr_detail_level"] = self.ocr_detail_combo.currentText()
        try:
            p["ocr_max_response_tokens"] = int(self.ocr_maxtok_spin.value())
        except (ValueError, TypeError):
            p["ocr_max_response_tokens"] = 4096
        # Image inpainting fields
        p["image_support"] = self.image_support_check.isChecked()
        p["image_base_url"] = self.image_base_edit.text().strip()
        p["image_model"] = self.image_model_edit.text().strip()
        p["image_prompt"] = self.image_prompt_edit.toPlainText().strip()

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
        right_layout.addWidget(ConfigSectionHeader(self.tr("Basic Settings")))
        form = QFormLayout()
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        self.name_edit = ConfigLineEdit()
        self.name_edit.setPlaceholderText(self.tr("e.g., My Custom API"))
        host_row = QHBoxLayout()
        self.host_edit = ConfigLineEdit()
        self.host_edit.setPlaceholderText("https://api.example.com/v1")
        test_btn = QPushButton(self.tr("Test"))
        test_btn.clicked.connect(self._on_test_connection)
        host_row.addWidget(self.host_edit, 1)
        host_row.addWidget(test_btn)
        self.key_edit = ConfigLineEdit()
        self.model_edit = ConfigLineEdit()
        self.model_edit.setPlaceholderText("gpt-4o, ...")
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_edit)
        fetch_btn = QPushButton(self.tr("Fetch Models"))
        fetch_btn.clicked.connect(self._on_fetch_models)
        model_row.addWidget(fetch_btn)
        self.vision_check = ConfigCheckBox(self.tr("Vision support (for OCR)"))
        self.vision_check.setToolTip(
            self.tr(
                "Enable this for models that can process images. Vision-capable profiles will appear in the OCR model selector."
            )
        )
        self.temp_edit = ConfigLineEdit()
        self.temp_edit.setPlaceholderText("0.1")
        self.topp_edit = ConfigLineEdit()
        self.topp_edit.setPlaceholderText("1.0")
        self.maxtok_edit = ConfigLineEdit()
        self.maxtok_edit.setPlaceholderText(self.tr("Unlimited (leave empty)"))
        self.reasoning_combo = ConfigComboBox()
        items = [
            (self.tr("默认"), ""),
            ("none", "none"),
            ("low", "low"),
            ("medium", "medium"),
            ("high", "high"),
            ("xhigh", "xhigh"),
            ("max", "max"),
        ]
        for display, data in items:
            self.reasoning_combo.addItem(display, data)
        self.reasoning_combo.setToolTip(
            self.tr(
                "Override the model's reasoning/thinking effort.\nLeave as \"default\" to let the API decide.\nThis maps automatically to each provider's native parameter\n(OpenAI reasoning_effort, Claude output_config.effort, etc.)."
            )
        )
        form.addRow(self.tr("Name:"), self.name_edit)
        form.addRow(self.tr("Host:"), host_row)
        form.addRow(self.tr("API Key:"), self.key_edit)
        form.addRow(self.tr("Model:"), model_row)
        form.addRow("", self.vision_check)
        form.addRow(self.tr("Temperature:"), self.temp_edit)
        form.addRow(self.tr("Top P:"), self.topp_edit)
        form.addRow(self.tr("Max Tokens:"), self.maxtok_edit)
        form.addRow(self.tr("Reasoning Effort:"), self.reasoning_combo)
        right_layout.addLayout(form)

        # Connection & Rate Limiting
        right_layout.addWidget(ConfigSectionHeader(self.tr("Connection & Rate Limiting")))
        conn_form = QFormLayout()
        conn_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        conn_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        self.proxy_edit = ConfigLineEdit()
        self.proxy_edit.setPlaceholderText("http://user:pass@host:port")
        self.rpm_spin = NoArrowsSpinBox()
        self.rpm_spin.setRange(0, 10000)
        self.rpm_spin.setValue(20)
        self.rpm_spin.setToolTip(self.tr("0 = unlimited"))
        self.delay_spin = NoArrowsDoubleSpinBox()
        self.delay_spin.setRange(0, 60)
        self.delay_spin.setSingleStep(0.1)
        self.delay_spin.setDecimals(2)
        self.delay_spin.setValue(0.3)
        conn_form.addRow(self.tr("Proxy:"), self.proxy_edit)
        conn_form.addRow(self.tr("Requests/min:"), self.rpm_spin)
        conn_form.addRow(self.tr("Delay (s):"), self.delay_spin)
        right_layout.addLayout(conn_form)

        # Return JSON Schema checkbox
        self.return_json_schema_check = ConfigCheckBox(
            self.tr("Return JSON Schema")
        )
        self.return_json_schema_check.setToolTip(
            self.tr("When enabled, the API response is validated against a strict JSON schema. Disable for broader compatibility with non-OpenAI providers.")
        )
        right_layout.addWidget(self.return_json_schema_check)

        # Extra translation instructions (optional)
        right_layout.addWidget(
            ConfigSectionHeader(self.tr("Extra Translation Instructions (optional)"))
        )
        sp_form = QFormLayout()
        sp_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        sp_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        self.system_prompt_edit = ConfigTextEdit()
        self.system_prompt_edit.setPlaceholderText(
            self.tr("Optional custom instructions appended to the system prompt. Leave empty to use the default translation contract.")
        )
        self.system_prompt_edit.setMinimumHeight(80)
        sp_form.addRow(self.tr("Instructions:"), self.system_prompt_edit)
        right_layout.addLayout(sp_form)

        # OCR Settings
        right_layout.addWidget(ConfigSectionHeader(self.tr("OCR Settings (optional)")))
        ocr_form = QFormLayout()
        ocr_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        ocr_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        self.ocr_prompt_edit = ConfigTextEdit()
        self.ocr_prompt_edit.setPlaceholderText(
            self.tr("OCR prompt with {language} placeholder.")
        )
        self.ocr_prompt_edit.setMinimumHeight(80)
        self.ocr_sysprompt_edit = ConfigTextEdit()
        self.ocr_sysprompt_edit.setPlaceholderText(
            self.tr("Optional system prompt for OCR.")
        )
        self.ocr_sysprompt_edit.setMinimumHeight(60)
        self.ocr_detail_combo = ConfigComboBox()
        self.ocr_detail_combo.addItems(["auto", "low", "high"])
        self.ocr_detail_combo.setCurrentText("auto")
        self.ocr_maxtok_spin = NoArrowsSpinBox()
        self.ocr_maxtok_spin.setRange(64, 131072)
        self.ocr_maxtok_spin.setValue(4096)
        ocr_form.addRow(self.tr("OCR Prompt:"), self.ocr_prompt_edit)
        ocr_form.addRow(self.tr("OCR System Prompt:"), self.ocr_sysprompt_edit)
        ocr_form.addRow(self.tr("Detail Level:"), self.ocr_detail_combo)
        ocr_form.addRow(self.tr("Max Tokens:"), self.ocr_maxtok_spin)
        right_layout.addLayout(ocr_form)

        # Image Inpainting Settings (optional)
        right_layout.addWidget(
            ConfigSectionHeader(self.tr("Image Inpainting Settings (optional)"))
        )
        self.image_support_check = ConfigCheckBox(
            self.tr("Enable image inpainting for this profile")
        )
        self.image_support_check.setToolTip(
            self.tr("Enable this for models that can generate/clean images. Image-capable profiles appear in the online inpainter's profile selector.")
        )
        right_layout.addWidget(self.image_support_check)

        self.image_fields = QWidget()
        image_form = QFormLayout(self.image_fields)
        image_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        image_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        self.image_base_edit = ConfigLineEdit()
        self.image_base_edit.setPlaceholderText("https://api.openai.com/v1/images/edits")
        image_test_btn = QPushButton(self.tr("Test"))
        image_test_btn.setObjectName("ConfigButton")
        image_test_btn.setFixedHeight(28)
        image_test_btn.clicked.connect(self._on_test_image_connection)
        image_base_row = QHBoxLayout()
        image_base_row.addWidget(self.image_base_edit, 1)
        image_base_row.addWidget(image_test_btn)
        self.image_model_edit = ConfigLineEdit()
        self.image_model_edit.setPlaceholderText("gpt-image-2, ...")
        image_fetch_btn = QPushButton(self.tr("Fetch Models"))
        image_fetch_btn.setObjectName("ConfigButton")
        image_fetch_btn.setFixedHeight(28)
        image_fetch_btn.clicked.connect(self._on_fetch_image_models)
        image_model_row = QHBoxLayout()
        image_model_row.addWidget(self.image_model_edit, 1)
        image_model_row.addWidget(image_fetch_btn)
        self.image_prompt_edit = ConfigTextEdit()
        self.image_prompt_edit.setPlaceholderText(
            self.tr("Optional prompt sent with each inpainting request.")
        )
        self.image_prompt_edit.setMinimumHeight(80)
        image_form.addRow(self.tr("Image Endpoint:"), image_base_row)
        image_form.addRow(self.tr("Image Model:"), image_model_row)
        image_form.addRow(self.tr("Image Prompt:"), self.image_prompt_edit)
        right_layout.addWidget(self.image_fields)

        self.image_support_check.toggled.connect(self._on_image_support_toggled)
        self._on_image_support_toggled(self.image_support_check.isChecked())

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
        for edit in [
            self.name_edit,
            self.host_edit,
            self.key_edit,
            self.model_edit,
            self.temp_edit,
            self.topp_edit,
            self.maxtok_edit,
            self.system_prompt_edit,
            self.proxy_edit,
            self.ocr_prompt_edit,
            self.ocr_sysprompt_edit,
        ]:
            edit.clear()
        self.vision_check.setChecked(False)
        self.return_json_schema_check.setChecked(False)
        self.reasoning_combo.setCurrentIndex(0)  # 默认
        self.ocr_detail_combo.setCurrentText("auto")
        self.rpm_spin.setValue(20)
        self.delay_spin.setValue(0.3)
        self.ocr_maxtok_spin.setValue(4096)
        self.image_support_check.setChecked(False)
        self.image_base_edit.clear()
        self.image_model_edit.clear()
        self.image_prompt_edit.setPlainText(DEFAULT_INPAINT_PROMPT)

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
        self.return_json_schema_check.setChecked(p.get("return_json_schema", False))
        re_val = p.get("reasoning_effort", "")
        re_idx = self.reasoning_combo.findData(re_val)
        self.reasoning_combo.setCurrentIndex(re_idx if re_idx >= 0 else 0)
        self.system_prompt_edit.setPlainText(p.get("system_prompt", ""))
        # OCR fields
        self.ocr_prompt_edit.setPlainText(p.get("ocr_prompt", ""))
        self.ocr_sysprompt_edit.setPlainText(p.get("ocr_system_prompt", ""))
        self.ocr_detail_combo.setCurrentText(p.get("ocr_detail_level", "auto"))
        try:
            self.ocr_maxtok_spin.setValue(int(p.get("ocr_max_response_tokens", 4096)))
        except (ValueError, TypeError):
            self.ocr_maxtok_spin.setValue(4096)
        self.image_support_check.setChecked(p.get("image_support", False))
        self.image_base_edit.setText(p.get("image_base_url", ""))
        self.image_model_edit.setText(p.get("image_model", ""))
        self.image_prompt_edit.setPlainText(
            p.get("image_prompt", DEFAULT_INPAINT_PROMPT)
        )
        self._update_delete_button()

    def _on_fetch_models(self):
        host = self.host_edit.text().strip()
        key = self.key_edit.text().strip()
        if not host or not key:
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("Host and API key are required to fetch the model list."),
            )
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
                        QMessageBox.information(
                            self, self.tr("Notice"), self.tr("No models found.")
                        )
                        return
                    dlg = FilterableListDialog(
                        self, self.tr("Select Model"), names
                    )
                    if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected:
                        self.model_edit.setText(dlg.selected)
                else:
                    QMessageBox.warning(
                        self,
                        self.tr("Error"),
                        self.tr("Failed to fetch model list. HTTP {code}").format(
                            code=resp.status_code
                        ),
                    )
        except Exception as e:
            QMessageBox.warning(
                self,
                self.tr("Error"),
                self.tr("Failed to fetch model list: {err}").format(err=e),
            )

    def _on_test_connection(self):
        host = self.host_edit.text().strip()
        key = self.key_edit.text().strip()
        if not host:
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("Host is required."),
            )
            return
        if not key:
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("A valid API key is required to test the connection."),
            )
            return
        # Determine proxy from the current profile
        proxy = ""
        row = self._current_row
        if 0 <= row < len(self._profiles):
            proxy = self._profiles[row].get("proxy", "")
        try:
            client_kwargs = {"timeout": 10}
            if proxy:
                client_kwargs["proxy"] = proxy
            with httpx.Client(**client_kwargs) as client:
                resp = client.get(
                    f"{host.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
                if resp.status_code == 200:
                    QMessageBox.information(
                        self,
                        self.tr("Connection Successful"),
                        self.tr(
                            "Connected! API is reachable and credentials are valid."
                        ),
                    )
                else:
                    QMessageBox.warning(
                        self,
                        self.tr("Connection Failed"),
                        self.tr("HTTP {code}: {text}").format(
                            code=resp.status_code, text=resp.text[:200]
                        ),
                    )
        except httpx.ConnectError:
            QMessageBox.warning(
                self,
                self.tr("Connection Failed"),
                self.tr(
                    "Could not connect to {host}.\nPlease check the URL and your network."
                ).format(host=host),
            )
        except httpx.TimeoutException:
            QMessageBox.warning(
                self,
                self.tr("Connection Failed"),
                self.tr("Connection timed out. Check the URL and network."),
            )
        except Exception as e:
            QMessageBox.warning(
                self,
                self.tr("Connection Failed"),
                self.tr("Error: {err}").format(err=e),
            )

    def _on_image_support_toggled(self, checked: bool):
        self.image_fields.setVisible(bool(checked))

    def _on_test_image_connection(self):
        base_url = self.image_base_edit.text().strip()
        key = self.key_edit.text().strip()
        proxy = ""
        row = self._current_row
        if 0 <= row < len(self._profiles):
            proxy = self._profiles[row].get("proxy", "")
        if not base_url:
            QMessageBox.warning(
                self, self.tr("Warning"), self.tr("Image endpoint is required.")
            )
            return
        try:
            fetch_image_models(base_url, key, proxy=proxy)
            QMessageBox.information(
                self,
                self.tr("Connection Successful"),
                self.tr("Connected! API is reachable and credentials are valid."),
            )
        except httpx.HTTPStatusError as e:
            QMessageBox.warning(
                self,
                self.tr("Connection Failed"),
                self.tr("HTTP {code}: {text}").format(
                    code=e.response.status_code, text=e.response.text[:200]
                ),
            )
        except httpx.ConnectError:
            QMessageBox.warning(
                self,
                self.tr("Connection Failed"),
                self.tr(
                    "Could not connect to {host}.\nPlease check the URL and your network."
                ).format(host=base_url),
            )
        except httpx.TimeoutException:
            QMessageBox.warning(
                self,
                self.tr("Connection Failed"),
                self.tr("Connection timed out. Check the URL and network."),
            )
        except Exception as e:
            QMessageBox.warning(
                self,
                self.tr("Connection Failed"),
                self.tr("Error: {err}").format(err=e),
            )

    def _on_fetch_image_models(self):
        base_url = self.image_base_edit.text().strip()
        key = self.key_edit.text().strip()
        proxy = ""
        row = self._current_row
        if 0 <= row < len(self._profiles):
            proxy = self._profiles[row].get("proxy", "")
        if not base_url:
            QMessageBox.warning(
                self, self.tr("Warning"), self.tr("Image endpoint is required.")
            )
            return
        try:
            names = fetch_image_models(base_url, key, proxy=proxy)
            if names:
                dlg = FilterableListDialog(self, self.tr("Select Model"), names)
                if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected:
                    self.image_model_edit.setText(dlg.selected)
            else:
                QMessageBox.information(
                    self, self.tr("Notice"), self.tr("No models found.")
                )
        except Exception as e:
            QMessageBox.warning(
                self,
                self.tr("Error"),
                self.tr("Failed to fetch model list: {err}").format(err=e),
            )

    def _on_delete(self):
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self._profiles) or self._is_builtin(row):
            return
        name = self._profiles[row].get("name", "")
        reply = QMessageBox.question(
            self,
            self.tr("Confirm Delete"),
            self.tr('Delete profile "{name}"?').format(name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
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


# ── Inline widget for page use ──


class ProfileManagerWidget(QWidget):
    """Inline profile manager widget for use inside ConfigPanel pages.

    Features a top toolbar (profile combo + action buttons) and a scrollable
    edit form. Auto-saves on profile switch / add / delete / hide.
    """

    profiles_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._profiles = load_profiles()
        self._current_idx = -1
        self._suppress_save = False  # prevent recursive saves during form population
        self._build_ui()

    # ── helpers ──

    def _is_builtin(self, idx: int) -> bool:
        return 0 <= idx < len(self._profiles) and self._profiles[idx].get(
            "builtin", False
        )

    def _save_current_form(self):
        if self._suppress_save:
            return
        idx = self._current_idx
        if idx < 0 or idx >= len(self._profiles):
            return
        p = self._profiles[idx]
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
        p["reasoning_effort"] = self.reasoning_combo.currentData() or ""
        p["return_json_schema"] = self.return_json_schema_check.isChecked()
        p["system_prompt"] = self.system_prompt_edit.toPlainText().strip()
        try:
            p["temperature"] = float(self.temp_edit.text() or "0.1")
        except ValueError:
            p["temperature"] = 0.1
        try:
            p["top_p"] = float(self.topp_edit.text() or "1.0")
        except ValueError:
            p["top_p"] = 1.0
        p["max_tokens"] = self.maxtok_edit.text().strip()
        # OCR-specific fields
        p["ocr_prompt"] = self.ocr_prompt_edit.toPlainText().strip()
        p["ocr_system_prompt"] = self.ocr_sysprompt_edit.toPlainText().strip()
        p["ocr_detail_level"] = self.ocr_detail_combo.currentText()
        try:
            p["ocr_max_response_tokens"] = int(self.ocr_maxtok_spin.value())
        except (ValueError, TypeError):
            p["ocr_max_response_tokens"] = 4096
        # Image inpainting fields
        p["image_support"] = self.image_support_check.isChecked()
        p["image_base_url"] = self.image_base_edit.text().strip()
        p["image_model"] = self.image_model_edit.text().strip()
        p["image_prompt"] = self.image_prompt_edit.toPlainText().strip()

    def _persist(self):
        """Write current profiles to config and notify listeners."""
        save_all_profiles(self._profiles)
        self.profiles_changed.emit()

    # ── build UI ──

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Toolbar ──
        toolbar = QWidget()
        toolbar.setObjectName("ConfigContent")
        toolbar.setFixedHeight(42)
        tlay = QHBoxLayout(toolbar)
        tlay.setContentsMargins(12, 4, 12, 4)
        tlay.setSpacing(8)

        tlay.addWidget(QLabel(self.tr("Profile:")))
        self.profile_combo = ConfigComboBox()
        self.profile_combo.setMinimumWidth(200)
        self.profile_combo.currentIndexChanged.connect(self._on_combo_select)
        tlay.addWidget(self.profile_combo)

        self.add_btn = QPushButton(self.tr("+ Add"))
        self.add_btn.setObjectName("ConfigButton")
        self.add_btn.setFixedHeight(28)
        self.add_btn.clicked.connect(self._on_add)
        tlay.addWidget(self.add_btn)

        self.delete_btn = QPushButton(self.tr("Delete"))
        self.delete_btn.setObjectName("ConfigButton")
        self.delete_btn.setFixedHeight(28)
        self.delete_btn.clicked.connect(self._on_delete)
        tlay.addWidget(self.delete_btn)

        restore_btn = QPushButton(self.tr("Restore Builtins"))
        restore_btn.setObjectName("ConfigButton")
        restore_btn.setFixedHeight(28)
        restore_btn.clicked.connect(self._on_restore_builtins)
        tlay.addWidget(restore_btn)

        tlay.addStretch()
        layout.addWidget(toolbar)

        # ── Separator line ──
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: @borderColor;")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # ── Scrollable form ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        form_widget = QWidget()
        form_layout = QVBoxLayout(form_widget)
        form_layout.setContentsMargins(24, 16, 24, 24)
        form_layout.setSpacing(6)

        # Basic Settings
        form_layout.addWidget(ConfigSectionHeader(self.tr("Basic Settings")))
        basic_form = QFormLayout()
        basic_form.setSpacing(8)
        basic_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        basic_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )

        self.name_edit = ConfigLineEdit()
        self.name_edit.setPlaceholderText(self.tr("e.g., My Custom API"))
        host_row = QHBoxLayout()
        self.host_edit = ConfigLineEdit()
        self.host_edit.setPlaceholderText("https://api.example.com/v1")
        test_btn = QPushButton(self.tr("Test"))
        test_btn.setObjectName("ConfigButton")
        test_btn.setFixedHeight(28)
        test_btn.clicked.connect(self._on_test_connection)
        host_row.addWidget(self.host_edit, 1)
        host_row.addWidget(test_btn)
        self.key_edit = ConfigLineEdit()
        self.model_edit = ConfigLineEdit()
        self.model_edit.setPlaceholderText("gpt-4o, ...")
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_edit)
        fetch_btn = QPushButton(self.tr("Fetch Models"))
        fetch_btn.setObjectName("ConfigButton")
        fetch_btn.setFixedHeight(28)
        fetch_btn.clicked.connect(self._on_fetch_models)
        model_row.addWidget(fetch_btn)
        self.vision_check = ConfigCheckBox(self.tr("Vision support (for OCR)"))
        self.vision_check.setToolTip(
            self.tr("Enable this for models that can process images. Vision-capable profiles will appear in the OCR model selector.")
        )
        self.temp_edit = ConfigLineEdit()
        self.temp_edit.setPlaceholderText("0.1")
        self.topp_edit = ConfigLineEdit()
        self.topp_edit.setPlaceholderText("1.0")
        self.maxtok_edit = ConfigLineEdit()
        self.maxtok_edit.setPlaceholderText(self.tr("Unlimited (leave empty)"))
        self.reasoning_combo = ConfigComboBox()
        items = [
            (self.tr("默认"), ""),
            ("none", "none"),
            ("low", "low"),
            ("medium", "medium"),
            ("high", "high"),
            ("xhigh", "xhigh"),
            ("max", "max"),
        ]
        for display, data in items:
            self.reasoning_combo.addItem(display, data)
        self.reasoning_combo.setToolTip(
            self.tr("Override the model's reasoning/thinking effort.\nLeave as \"default\" to let the API decide.\nThis maps automatically to each provider's native parameter\n(OpenAI reasoning_effort, Claude output_config.effort, etc.).")
        )
        basic_form.addRow(self.tr("Name:"), self.name_edit)
        basic_form.addRow(self.tr("Host:"), host_row)
        basic_form.addRow(self.tr("API Key:"), self.key_edit)
        basic_form.addRow(self.tr("Model:"), model_row)
        basic_form.addRow("", self.vision_check)
        basic_form.addRow(self.tr("Temperature:"), self.temp_edit)
        basic_form.addRow(self.tr("Top P:"), self.topp_edit)
        basic_form.addRow(self.tr("Max Tokens:"), self.maxtok_edit)
        basic_form.addRow(self.tr("Reasoning Effort:"), self.reasoning_combo)
        form_layout.addLayout(basic_form)

        # Connection & Rate Limiting
        form_layout.addWidget(
            ConfigSectionHeader(self.tr("Connection & Rate Limiting"))
        )
        conn_form = QFormLayout()
        conn_form.setSpacing(8)
        conn_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        conn_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        self.proxy_edit = ConfigLineEdit()
        self.proxy_edit.setPlaceholderText("http://user:pass@host:port")
        self.rpm_spin = NoArrowsSpinBox()
        self.rpm_spin.setRange(0, 10000)
        self.rpm_spin.setValue(20)
        self.rpm_spin.setToolTip(self.tr("0 = unlimited"))
        self.delay_spin = NoArrowsDoubleSpinBox()
        self.delay_spin.setRange(0, 60)
        self.delay_spin.setSingleStep(0.1)
        self.delay_spin.setDecimals(2)
        self.delay_spin.setValue(0.3)
        conn_form.addRow(self.tr("Proxy:"), self.proxy_edit)
        conn_form.addRow(self.tr("Requests/min:"), self.rpm_spin)
        conn_form.addRow(self.tr("Delay (s):"), self.delay_spin)
        form_layout.addLayout(conn_form)

        # Return JSON Schema checkbox
        self.return_json_schema_check = ConfigCheckBox(
            self.tr("Return JSON Schema")
        )
        self.return_json_schema_check.setToolTip(
            self.tr("When enabled, the API response is validated against a strict JSON schema. Disable for broader compatibility with non-OpenAI providers.")
        )
        form_layout.addWidget(self.return_json_schema_check)

        # Extra translation instructions (optional)
        form_layout.addWidget(
            ConfigSectionHeader(self.tr("Extra Translation Instructions (optional)"))
        )
        sp_form = QFormLayout()
        sp_form.setSpacing(8)
        sp_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        sp_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        self.system_prompt_edit = ConfigTextEdit()
        self.system_prompt_edit.setPlaceholderText(
            self.tr("Optional custom instructions appended to the system prompt. Leave empty to use the default translation contract.")
        )
        self.system_prompt_edit.setMinimumHeight(80)
        sp_form.addRow(self.tr("Instructions:"), self.system_prompt_edit)
        form_layout.addLayout(sp_form)

        # OCR Settings
        form_layout.addWidget(
            ConfigSectionHeader(self.tr("OCR Settings (optional)"))
        )
        ocr_form = QFormLayout()
        ocr_form.setSpacing(8)
        ocr_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        ocr_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        self.ocr_prompt_edit = ConfigTextEdit()
        self.ocr_prompt_edit.setPlaceholderText(
            self.tr("OCR prompt with {language} placeholder.")
        )
        self.ocr_prompt_edit.setMinimumHeight(80)
        self.ocr_sysprompt_edit = ConfigTextEdit()
        self.ocr_sysprompt_edit.setPlaceholderText(
            self.tr("Optional system prompt for OCR.")
        )
        self.ocr_sysprompt_edit.setMinimumHeight(60)
        self.ocr_detail_combo = ConfigComboBox()
        self.ocr_detail_combo.addItems(["auto", "low", "high"])
        self.ocr_detail_combo.setCurrentText("auto")
        self.ocr_maxtok_spin = NoArrowsSpinBox()
        self.ocr_maxtok_spin.setRange(64, 131072)
        self.ocr_maxtok_spin.setValue(4096)
        ocr_form.addRow(self.tr("OCR Prompt:"), self.ocr_prompt_edit)
        ocr_form.addRow(self.tr("OCR System Prompt:"), self.ocr_sysprompt_edit)
        ocr_form.addRow(self.tr("Detail Level:"), self.ocr_detail_combo)
        ocr_form.addRow(self.tr("Max Tokens:"), self.ocr_maxtok_spin)
        form_layout.addLayout(ocr_form)

        # Image Inpainting Settings (optional)
        form_layout.addWidget(
            ConfigSectionHeader(self.tr("Image Inpainting Settings (optional)"))
        )
        self.image_support_check = ConfigCheckBox(
            self.tr("Enable image inpainting for this profile")
        )
        self.image_support_check.setToolTip(
            self.tr("Enable this for models that can generate/clean images. Image-capable profiles appear in the online inpainter's profile selector.")
        )
        form_layout.addWidget(self.image_support_check)

        self.image_fields = QWidget()
        image_form = QFormLayout(self.image_fields)
        image_form.setSpacing(8)
        image_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        image_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        self.image_base_edit = ConfigLineEdit()
        self.image_base_edit.setPlaceholderText("https://api.openai.com/v1/images/edits")
        image_test_btn = QPushButton(self.tr("Test"))
        image_test_btn.setObjectName("ConfigButton")
        image_test_btn.setFixedHeight(28)
        image_test_btn.clicked.connect(self._on_test_image_connection)
        image_base_row = QHBoxLayout()
        image_base_row.addWidget(self.image_base_edit, 1)
        image_base_row.addWidget(image_test_btn)
        self.image_model_edit = ConfigLineEdit()
        self.image_model_edit.setPlaceholderText("gpt-image-2, ...")
        image_fetch_btn = QPushButton(self.tr("Fetch Models"))
        image_fetch_btn.setObjectName("ConfigButton")
        image_fetch_btn.setFixedHeight(28)
        image_fetch_btn.clicked.connect(self._on_fetch_image_models)
        image_model_row = QHBoxLayout()
        image_model_row.addWidget(self.image_model_edit, 1)
        image_model_row.addWidget(image_fetch_btn)
        self.image_prompt_edit = ConfigTextEdit()
        self.image_prompt_edit.setPlaceholderText(
            self.tr("Optional prompt sent with each inpainting request.")
        )
        self.image_prompt_edit.setMinimumHeight(80)
        image_form.addRow(self.tr("Image Endpoint:"), image_base_row)
        image_form.addRow(self.tr("Image Model:"), image_model_row)
        image_form.addRow(self.tr("Image Prompt:"), self.image_prompt_edit)
        form_layout.addWidget(self.image_fields)

        self.image_support_check.toggled.connect(self._on_image_support_toggled)
        self._on_image_support_toggled(self.image_support_check.isChecked())

        form_layout.addStretch()
        scroll.setWidget(form_widget)
        layout.addWidget(scroll, 1)

        # Populate combo and select first
        self._refresh_combo()

    # ── combo / form sync ──

    def _refresh_combo(self):
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for p in self._profiles:
            label = p.get("name", "")
            if p.get("builtin"):
                label += self.tr(" (built-in)")
            self.profile_combo.addItem(label)
        self.profile_combo.blockSignals(False)
        if self._profiles:
            self.profile_combo.setCurrentIndex(0)
            self._on_combo_select(0)
        else:
            self._clear_fields()
            self._current_idx = -1

    def _clear_fields(self):
        for edit in [
            self.name_edit,
            self.host_edit,
            self.key_edit,
            self.model_edit,
            self.temp_edit,
            self.topp_edit,
            self.maxtok_edit,
            self.system_prompt_edit,
            self.proxy_edit,
            self.ocr_prompt_edit,
            self.ocr_sysprompt_edit,
        ]:
            edit.clear()
        self.vision_check.setChecked(False)
        self.return_json_schema_check.setChecked(False)
        self.reasoning_combo.setCurrentIndex(0)
        self.ocr_detail_combo.setCurrentText("auto")
        self.rpm_spin.setValue(20)
        self.delay_spin.setValue(0.3)
        self.ocr_maxtok_spin.setValue(4096)
        self.image_support_check.setChecked(False)
        self.image_base_edit.clear()
        self.image_model_edit.clear()
        self.image_prompt_edit.setPlainText(DEFAULT_INPAINT_PROMPT)

    def _populate_form(self, idx: int):
        if idx < 0 or idx >= len(self._profiles):
            return
        p = self._profiles[idx]
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
        self.return_json_schema_check.setChecked(p.get("return_json_schema", False))
        re_val = p.get("reasoning_effort", "")
        re_idx = self.reasoning_combo.findData(re_val)
        self.reasoning_combo.setCurrentIndex(re_idx if re_idx >= 0 else 0)
        self.system_prompt_edit.setPlainText(p.get("system_prompt", ""))
        # OCR
        self.ocr_prompt_edit.setPlainText(p.get("ocr_prompt", ""))
        self.ocr_sysprompt_edit.setPlainText(p.get("ocr_system_prompt", ""))
        self.ocr_detail_combo.setCurrentText(p.get("ocr_detail_level", "auto"))
        try:
            self.ocr_maxtok_spin.setValue(int(p.get("ocr_max_response_tokens", 4096)))
        except (ValueError, TypeError):
            self.ocr_maxtok_spin.setValue(4096)
        self.image_support_check.setChecked(p.get("image_support", False))
        self.image_base_edit.setText(p.get("image_base_url", ""))
        self.image_model_edit.setText(p.get("image_model", ""))
        self.image_prompt_edit.setPlainText(
            p.get("image_prompt", DEFAULT_INPAINT_PROMPT)
        )

    # ── slots ──

    def _on_combo_select(self, idx: int):
        if idx < 0 or idx >= len(self._profiles):
            return
        self._save_current_form()
        self._current_idx = idx
        self._suppress_save = True
        self._populate_form(idx)
        self._suppress_save = False
        self.delete_btn.setEnabled(not self._is_builtin(idx))

    def _on_add(self):
        self._save_current_form()
        new_name = self.tr("New Profile")
        # Ensure unique name
        existing = {p.get("name") for p in self._profiles}
        if new_name in existing:
            i = 1
            while f"{new_name} ({i})" in existing:
                i += 1
            new_name = f"{new_name} ({i})"
        new_profile = dict(SAMPLE_PROFILES[0])  # start with defaults
        new_profile["name"] = new_name
        new_profile["builtin"] = False
        self._profiles.append(new_profile)
        self._persist()
        self._refresh_combo()
        # Select the new profile
        self.profile_combo.setCurrentIndex(len(self._profiles) - 1)

    def _on_delete(self):
        idx = self.profile_combo.currentIndex()
        if idx < 0 or idx >= len(self._profiles) or self._is_builtin(idx):
            return
        name = self._profiles[idx].get("name", "")
        reply = QMessageBox.question(
            self,
            self.tr("Confirm Delete"),
            self.tr('Delete profile "{name}"?').format(name=name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        del self._profiles[idx]
        self._current_idx = -1
        self._persist()
        self._refresh_combo()

    def _on_restore_builtins(self):
        """Re-append any missing builtin profiles without removing user ones."""
        default_map = {p["name"]: dict(p) for p in SAMPLE_PROFILES if p.get("builtin")}
        existing_names = {p.get("name") for p in self._profiles}
        added = 0
        for name, defaults in default_map.items():
            if name not in existing_names:
                self._profiles.append(defaults)
                added += 1
        if added:
            self._persist()
            self._refresh_combo()
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

    # ── Background network helpers ──
    # Test / Fetch Models must not run the request on the GUI thread: a slow or
    # unreachable host froze the window (Windows "Not Responding"). These route
    # the request through ``_NetWorker`` and deliver the result back on the GUI
    # thread.

    def _current_proxy(self) -> str:
        idx = self._current_idx
        if 0 <= idx < len(self._profiles):
            return self._profiles[idx].get("proxy", "")
        return ""

    def _start_net(self, callback, on_ok, on_err):
        prev = getattr(self, "_net_worker", None)
        if prev is not None and prev.isRunning():
            return
        worker = _NetWorker(self, callback)
        worker.finished_ok.connect(on_ok)
        worker.finished_err.connect(on_err)
        self._net_worker = worker
        worker.start()

    def _net_error_message(self, e, host: str) -> str:
        if isinstance(e, httpx.HTTPStatusError):
            return self.tr("HTTP {code}: {text}").format(
                code=e.response.status_code, text=e.response.text[:200]
            )
        if isinstance(e, httpx.ConnectError):
            return self.tr(
                "Could not connect to {host}.\nPlease check the URL and your network."
            ).format(host=host)
        if isinstance(e, httpx.TimeoutException):
            return self.tr("Connection timed out. Check the URL and network.")
        return self.tr("Error: {err}").format(err=e)

    def _show_test_error(self, e, host: str):
        QMessageBox.warning(
            self, self.tr("Connection Failed"), self._net_error_message(e, host)
        )

    def _show_fetch_error(self, e, host: str):
        QMessageBox.warning(
            self,
            self.tr("Error"),
            self.tr("Failed to fetch model list: {err}").format(
                err=self._net_error_message(e, host)
            ),
        )

    def _test_success(self):
        QMessageBox.information(
            self,
            self.tr("Connection Successful"),
            self.tr("Connected! API is reachable and credentials are valid."),
        )

    def _models_fetch_ok(self, names):
        if not names:
            QMessageBox.information(
                self, self.tr("Notice"), self.tr("No models found.")
            )
            return
        dlg = FilterableListDialog(self, self.tr("Select Model"), names)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected:
            self.model_edit.setText(dlg.selected)
            self._save_current_form()
            self._persist()

    def _image_models_fetch_ok(self, names):
        if not names:
            QMessageBox.information(
                self, self.tr("Notice"), self.tr("No models found.")
            )
            return
        dlg = FilterableListDialog(self, self.tr("Select Model"), names)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected:
            self.image_model_edit.setText(dlg.selected)
            self._save_current_form()
            self._persist()

    def _on_fetch_models(self):
        host = self.host_edit.text().strip()
        key = self.key_edit.text().strip()
        if not host or not key:
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("Host and API key are required to fetch the model list."),
            )
            return
        proxy = self._current_proxy()
        self._start_net(
            lambda: _probe_model_list(host, key, proxy),
            self._models_fetch_ok,
            lambda e: self._show_fetch_error(e, host),
        )

    def _on_test_connection(self):
        host = self.host_edit.text().strip()
        key = self.key_edit.text().strip()
        if not host:
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("Host is required."),
            )
            return
        if not key:
            QMessageBox.warning(
                self,
                self.tr("Warning"),
                self.tr("A valid API key is required to test the connection."),
            )
            return
        proxy = self._current_proxy()
        self._start_net(
            lambda: _probe_connection(host, key, proxy),
            lambda _r: self._test_success(),
            lambda e: self._show_test_error(e, host),
        )

    def _on_image_support_toggled(self, checked: bool):
        self.image_fields.setVisible(bool(checked))

    def _on_test_image_connection(self):
        base_url = self.image_base_edit.text().strip()
        key = self.key_edit.text().strip()
        if not base_url:
            QMessageBox.warning(
                self, self.tr("Warning"), self.tr("Image endpoint is required.")
            )
            return
        proxy = self._current_proxy()
        self._start_net(
            lambda: fetch_image_models(base_url, key, proxy=proxy),
            lambda _r: self._test_success(),
            lambda e: self._show_test_error(e, base_url),
        )

    def _on_fetch_image_models(self):
        base_url = self.image_base_edit.text().strip()
        key = self.key_edit.text().strip()
        if not base_url:
            QMessageBox.warning(
                self, self.tr("Warning"), self.tr("Image endpoint is required.")
            )
            return
        proxy = self._current_proxy()
        self._start_net(
            lambda: fetch_image_models(base_url, key, proxy=proxy),
            self._image_models_fetch_ok,
            lambda e: self._show_fetch_error(e, base_url),
        )

    def hideEvent(self, event):
        """Auto-save when navigating away from this page."""
        self._save_current_form()
        self._persist()
        super().hideEvent(event)
