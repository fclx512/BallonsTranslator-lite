import re
import time
import json
from typing import List, Dict, Optional

import httpx
import openai
from pydantic import BaseModel, Field, ValidationError

try:
    from PyQt6.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QListWidget,
        QLineEdit, QPushButton, QLabel, QDialogButtonBox,
        QFormLayout, QWidget, QSplitter, QMessageBox,
        QComboBox, QInputDialog, QTextEdit, QScrollArea,
    )
except ImportError:
    from PyQt5.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QListWidget,
        QLineEdit, QPushButton, QLabel, QDialogButtonBox,
        QFormLayout, QWidget, QSplitter, QMessageBox,
        QComboBox, QInputDialog, QTextEdit, QScrollArea,
    )

from .base import BaseTranslator, register_translator


class TranslationElement(BaseModel):
    id: int = Field(..., description="The original numeric ID of the text snippet.")
    translation: str = Field(
        ..., description="The translated text corresponding to the id."
    )


class TranslationResponse(BaseModel):
    translations: List[TranslationElement] = Field(
        ..., description="A list of all translated elements."
    )


DEFAULT_SYSTEM_PROMPT = (
    'You are an expert translator. Your task is to accurately translate '
    'the given text snippets. You MUST provide the output strictly in the '
    'specified JSON format, without any additional explanations or markdown '
    'formatting. The JSON object must have a single key \'translations\', '
    'which is a list of objects, each with an \'id\' (integer) and a '
    '\'translation\' (string).\n\n'
    'Example Output Schema:\n'
    '{"translations": [{"id": 1, "translation": "Translated text here."}]}'
)

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

SAMPLE_PROFILES = [
    {"name": "OpenAI", "api_host": "https://api.openai.com/v1", "api_key": "", "model": "gpt-4o", "temperature": 0.1, "top_p": 1.0, "max_tokens": 4096, "prompt_template": DEFAULT_PROMPT_TEMPLATE, "chat_samples": DEFAULT_CHAT_SAMPLES},
    {"name": "Gemini", "api_host": "https://generativelanguage.googleapis.com/v1beta/openai", "api_key": "", "model": "gemini-2.5-flash", "temperature": 0.1, "top_p": 1.0, "max_tokens": 4096, "prompt_template": DEFAULT_PROMPT_TEMPLATE, "chat_samples": DEFAULT_CHAT_SAMPLES},
    {"name": "OpenRouter", "api_host": "https://openrouter.ai/api/v1", "api_key": "", "model": "", "temperature": 0.1, "top_p": 1.0, "max_tokens": 4096, "prompt_template": DEFAULT_PROMPT_TEMPLATE, "chat_samples": DEFAULT_CHAT_SAMPLES},
    {"name": "LM Studio", "api_host": "http://localhost:1234/v1", "api_key": "dummy-key", "model": "", "temperature": 0.1, "top_p": 1.0, "max_tokens": 4096, "prompt_template": DEFAULT_PROMPT_TEMPLATE, "chat_samples": DEFAULT_CHAT_SAMPLES, "builtin": True},
    {"name": "Ollama", "api_host": "http://localhost:11434/v1", "api_key": "dummy-key", "model": "", "temperature": 0.1, "top_p": 1.0, "max_tokens": 4096, "prompt_template": DEFAULT_PROMPT_TEMPLATE, "chat_samples": DEFAULT_CHAT_SAMPLES, "builtin": True},
]


@register_translator("LLM_API_Translator")
class LLM_API_Translator(BaseTranslator):
    concate_text = False
    cht_require_convert = True

    params = {
        "manage_profiles": {
            "type": "pushbtn",
            "value": False,
            "display_name": "Manage Profiles...",
            "description": "Add, edit, or delete API profiles",
        },
        "active_profile": {
            "type": "selector",
            "options": [],
            "value": "",
            "description": "Quickly switch between saved profiles",
        },
        "max_requests_per_minute": {
            "value": 20,
            "description": "Max requests per minute per API key",
        },
        "delay": {
            "value": 0.3,
            "description": "Delay between requests (seconds)",
        },
        "retry_attempts": {
            "value": 3,
            "description": "Retry count on API connection failure",
        },
        "retry_timeout": {
            "value": 15,
            "description": "Retry wait time (seconds)",
        },
        "invalid_repeat_count": {
            "value": 2,
            "description": "Retry count on translation count mismatch",
        },
        "proxy": {
            "value": "",
            "description": "Proxy address (e.g. http(s)://user:password@host:port)",
        },
        "description": (
            "Generic OpenAI-compatible API connector. Create profiles to "
            "save API settings and switch between configurations."
        ),
    }

    def _setup_translator(self):
        self.lang_map = {
            "Auto Detect": "Auto",
            "简体中文": "Simplified Chinese",
            "繁體中文": "Traditional Chinese",
            "日本語": "Japanese",
            "English": "English",
            "한국어": "Korean",
            "Tiếng Việt": "Vietnamese",
            "čeština": "Czech",
            "Français": "French",
            "Deutsch": "German",
            "magyar nyelv": "Hungarian",
            "Italiano": "Italian",
            "Polski": "Polish",
            "Português": "Portuguese",
            "limba română": "Romanian",
            "русский язык": "Russian",
            "Español": "Spanish",
            "Türk dili": "Turkish",
            "украї́нська мо́ва": "Ukrainian",
            "Thai": "Thai",
            "Arabic": "Arabic",
            "Malayalam": "Malayalam",
            "Tamil": "Tamil",
            "Hindi": "Hindi",
        }
        self.token_count = 0
        self.token_count_last = 0
        self.current_key_index = 0
        self.last_request_time = 0
        self.request_count_minute = 0
        self.minute_start_time = time.time()
        self.key_usage = {}
        self.client = None
        # Load profiles from saved params or use defaults
        self._load_profiles()
        self._refresh_active_profile_options()

    # --- Profile Storage ---

    def _profiles_key(self) -> str:
        return "_profiles_storage"

    def _get_profiles_raw(self) -> str:
        key = self._profiles_key()
        if key not in self.params and hasattr(self, 'params'):
            return ""
        if key not in self.params:
            return ""
        val = self.params[key]
        if isinstance(val, dict):
            return val.get("value", "")
        return val

    def _set_profiles_raw(self, raw: str):
        key = self._profiles_key()
        if key not in self.params or not isinstance(self.params[key], dict):
            self.params[key] = {"value": raw}
        else:
            self.params[key]["value"] = raw

    def _load_profiles(self):
        raw = self._get_profiles_raw()
        if raw:
            try:
                self._profiles_data = json.loads(raw)
                if not isinstance(self._profiles_data, list):
                    self._profiles_data = list(SAMPLE_PROFILES)
                else:
                    self._merge_builtin_defaults()
            except (json.JSONDecodeError, TypeError):
                self._profiles_data = list(SAMPLE_PROFILES)
        else:
            self._profiles_data = list(SAMPLE_PROFILES)
            self._serialize_profiles()

    def _merge_builtin_defaults(self):
        """Ensure builtin profiles retain default fields from SAMPLE_PROFILES."""
        default_map = {
            p["name"]: p
            for p in SAMPLE_PROFILES
            if p.get("builtin")
        }
        for profile in self._profiles_data:
            if profile.get("builtin") and profile["name"] in default_map:
                defaults = default_map[profile["name"]]
                for key, val in defaults.items():
                    if key not in profile:
                        profile[key] = val

    def _serialize_profiles(self):
        self._set_profiles_raw(json.dumps(self._profiles_data, ensure_ascii=False))

    def _get_profile_names(self) -> List[str]:
        return [p.get("name", "") for p in self._profiles_data if p.get("name")]

    def _find_profile(self, name: str) -> Optional[Dict]:
        for p in self._profiles_data:
            if p.get("name") == name:
                return p
        return None

    def _refresh_active_profile_options(self):
        names = self._get_profile_names()
        self.params["active_profile"]["options"] = names
        if names and not self.params["active_profile"].get("value"):
            self.params["active_profile"]["value"] = names[0]

    # --- Active Profile Accessors ---

    @property
    def _active_profile(self) -> Dict:
        name = self.get_param_value("active_profile")
        if not name:
            return {}
        return self._find_profile(name) or {}

    @property
    def _effective_api_host(self) -> str:
        return (self._active_profile.get("api_host") or "").strip()

    @property
    def _is_local_endpoint(self) -> bool:
        host = self._effective_api_host
        return host and ("localhost" in host or "127.0.0.1" in host)

    @property
    def _effective_api_key(self) -> str:
        return self._active_profile.get("api_key") or ""

    @property
    def _effective_model(self) -> str:
        return self._active_profile.get("model") or ""

    # --- Standard properties ---

    @property
    def temperature(self) -> float:
        return float(self._active_profile.get("temperature", 0.1))

    @property
    def top_p(self) -> float:
        return float(self._active_profile.get("top_p", 1.0))

    @property
    def max_tokens(self) -> int:
        return int(self._active_profile.get("max_tokens", 4096))

    @property
    def max_rpm(self) -> int:
        return int(self.get_param_value("max_requests_per_minute"))

    @property
    def global_delay(self) -> float:
        return float(self.get_param_value("delay"))

    @property
    def retry_attempts(self) -> int:
        return int(self.get_param_value("retry_attempts"))

    @property
    def retry_timeout(self) -> int:
        return int(self.get_param_value("retry_timeout"))

    @property
    def invalid_repeat_count(self) -> int:
        return int(self.get_param_value("invalid_repeat_count"))

    @property
    def proxy(self) -> str:
        return self.get_param_value("proxy")

    # --- API Key Management ---

    def _respect_key_limit(self, key: str) -> bool:
        rpm = self.max_rpm
        if rpm <= 0:
            return True
        now = time.time()
        count, start_time = self.key_usage.get(key, (0, now))
        if now - start_time >= 60:
            count, start_time = 0, now
            self.key_usage[key] = (count, start_time)
        if count >= rpm:
            wait_time = 60.1 - (now - start_time)
            if wait_time > 0:
                self.logger.warning(
                    f"RPM limit ({rpm}) reached for key ...{key[-4:]}. "
                    f"Waiting {wait_time:.1f}s."
                )
                time.sleep(wait_time)
            self.key_usage[key] = (0, time.time())
            return False
        return True

    def _select_api_key(self) -> Optional[str]:
        single_key = self._effective_api_key
        if "multiple_keys" in self.params:
            keys_str = self.get_param_value("multiple_keys") or ""
        else:
            keys_str = ""
        api_keys = [
            k.strip()
            for k in keys_str.strip().replace("\n", ";").split(";")
            if k.strip()
        ]
        if not api_keys and not single_key:
            # Local endpoints (LM Studio, Ollama) don't need a real key
            if self._is_local_endpoint:
                return "dummy-key"
            self.logger.error(
                "No API key found. Add an api_key to the profile or fill multiple_keys."
            )
            return None
        if not api_keys:
            if self._respect_key_limit(single_key):
                now = time.time()
                count, start_time = self.key_usage.get(single_key, (0, now))
                if now - start_time >= 60:
                    count, start_time = 0, now
                self.key_usage[single_key] = (count + 1, start_time)
                return single_key
            return None
        start_index = self.current_key_index
        for i in range(len(api_keys)):
            index = (start_index + i) % len(api_keys)
            key = api_keys[index]
            if self._respect_key_limit(key):
                now = time.time()
                count, start_time = self.key_usage.get(key, (0, now))
                self.key_usage[key] = (count + 1, start_time)
                self.current_key_index = (index + 1) % len(api_keys)
                return key
        self.logger.error("All API keys are currently rate-limited.")
        return None

    # --- Rate Limiting ---

    def _respect_delay(self):
        now = time.time()
        rpm = self.max_rpm
        delay = self.global_delay
        if rpm > 0:
            if now - self.minute_start_time >= 60:
                self.request_count_minute = 0
                self.minute_start_time = now
            if self.request_count_minute >= rpm:
                wait = 60.1 - (now - self.minute_start_time)
                if wait > 0:
                    time.sleep(wait)
                self.request_count_minute = 0
                self.minute_start_time = time.time()
        elapsed = now - self.last_request_time
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self.last_request_time = time.time()
        self.request_count_minute += 1

    # --- Client ---

    def _initialize_client(self, api_key: str) -> bool:
        endpoint = self._effective_api_host
        if not endpoint:
            self.logger.error(
                "No api_host configured in the active profile."
            )
            return False

        proxy = self.proxy
        http_client = None
        if proxy:
            try:
                proxy_mounts = {
                    "http://": httpx.HTTPTransport(proxy=proxy),
                    "https://": httpx.HTTPTransport(proxy=proxy),
                }
                http_client = httpx.Client(mounts=proxy_mounts)
            except Exception as e:
                self.logger.error(f"Failed to initialize proxy '{proxy}': {e}")

        masked_key = (
            api_key[:4] + "..." + api_key[-4:]
            if len(api_key) > 8
            else api_key
        )
        self.logger.debug(
            f"Initializing client with key {masked_key} at endpoint {endpoint}"
        )

        try:
            self.client = openai.OpenAI(
                api_key=api_key,
                base_url=endpoint,
                http_client=http_client,
            )
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize client: {e}")
            self.client = None
            return False

    # --- Prompt Assembly ---

    def _assemble_prompts(self, queries: List[str], to_lang: str):
        from_lang = self.lang_map.get(self.lang_source, self.lang_source)
        from_lang_display = "the source language" if from_lang == "Auto" else from_lang
        input_elements = [
            {"id": i + 1, "source": query} for i, query in enumerate(queries)
        ]
        input_json_str = json.dumps(input_elements, ensure_ascii=False, indent=2)
        profile = self._active_profile
        template = profile.get("prompt_template", "")
        if template:
            try:
                prompt = template.format(
                    to_lang=to_lang,
                    from_lang=from_lang_display,
                    input_json=input_json_str,
                )
                yield prompt, len(queries)
                return
            except (KeyError, ValueError):
                pass
        prompt = (
            f"Please translate the following text snippets from "
            f"{from_lang_display} to "
            f"{to_lang}. The input is provided as a JSON array. Respond with a "
            f"JSON object in the specified format.\n\n"
            f"INPUT:\n{input_json_str}"
        )
        yield prompt, len(queries)

    # --- Chat Samples ---

    def _parse_chat_samples(self) -> List[Dict]:
        profile = self._active_profile
        samples_text = profile.get("chat_samples", "")
        if not samples_text:
            return []
        try:
            import yaml
            samples = yaml.load(samples_text, Loader=yaml.FullLoader)
        except Exception:
            return []
        src_tgt = f"{self.lang_source}-{self.lang_target}"
        if src_tgt not in samples:
            return []
        src_list = samples[src_tgt].get("source", [])
        tgt_list = samples[src_tgt].get("target", [])
        if not src_list or not tgt_list:
            return []
        input_elems = [
            {"id": i + 1, "source": s} for i, s in enumerate(src_list)
        ]
        output_elems = [
            {"id": i + 1, "translation": t} for i, t in enumerate(tgt_list)
        ]
        return [
            {"role": "user", "content": json.dumps(input_elems, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps(
                {"translations": output_elems}, ensure_ascii=False
            )},
        ]

    # --- API Call ---

    def _request_translation(self, prompt: str):
        current_api_key = self._select_api_key()
        if not current_api_key:
            raise ConnectionError(
                "No available API key. Check the active profile's api_key field."
            )

        if not self.client or self.client.api_key != current_api_key:
            if not self._initialize_client(current_api_key):
                raise ConnectionError("Failed to initialize API client.")

        self._respect_delay()

        profile = self._active_profile
        system_prompt = (
            profile.get("system_prompt") or
            ("system_prompt" in self.params and self.get_param_value("system_prompt")) or
            DEFAULT_SYSTEM_PROMPT
        )
        messages = [
            {"role": "system", "content": system_prompt},
        ]
        samples = self._parse_chat_samples()
        messages.extend(samples)
        messages.append({"role": "user", "content": prompt})

        api_args = {
            "model": self._effective_model,
            "messages": messages,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
        }
        rf = profile.get("response_format", "")
        if rf == "json_schema":
            api_args["response_format"] = {
                "type": "json_schema",
                "json_schema": {"schema": TranslationResponse.model_json_schema()},
            }
        elif self._is_local_endpoint:
            # Local engines (llama.cpp, LM Studio, Ollama) don't support
            # "json_object"; use "json_schema" instead.
            api_args["response_format"] = {
                "type": "json_schema",
                "json_schema": {"schema": TranslationResponse.model_json_schema()},
            }
        else:
            api_args["response_format"] = {"type": "json_object"}
        fp = profile.get("frequency_penalty")
        if fp is not None:
            api_args["frequency_penalty"] = float(fp)
        pp = profile.get("presence_penalty")
        if pp is not None:
            api_args["presence_penalty"] = float(pp)

        try:
            completion = self.client.chat.completions.create(**api_args)
        except Exception as e:
            self.logger.error(f"API request failed: {e}")
            raise

        if completion.usage:
            self.token_count += completion.usage.total_tokens
            self.token_count_last = completion.usage.total_tokens
        else:
            self.token_count_last = 0

        raw_content = (
            completion.choices[0].message.content
            if completion.choices and completion.choices[0].message
            else ""
        )
        if not raw_content:
            self.logger.warning("No message content in API response.")
            return None

        json_str = raw_content.strip()
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", json_str, re.DOTALL)
        if match:
            json_str = match.group(1)
        else:
            start = json_str.find("{")
            end = json_str.rfind("}")
            if start != -1 and end > start:
                json_str = json_str[start : end + 1]

        try:
            data = json.loads(json_str)
            validated = TranslationResponse.model_validate(data)
        except (ValidationError, json.JSONDecodeError) as e:
            self.logger.warning(
                f"Pydantic validation failed: {e}. Attempting to fix format."
            )
            try:
                simple_data = json.loads(json_str)
                fixed_translations = []
                if isinstance(simple_data, dict) and all(
                    k.isdigit() for k in simple_data.keys()
                ):
                    fixed_translations = [
                        {"id": int(k), "translation": v}
                        for k, v in simple_data.items()
                    ]
                elif isinstance(simple_data, list):
                    fixed_translations = simple_data
                elif "translations" not in simple_data:
                    for key in simple_data:
                        val = simple_data[key]
                        if isinstance(val, str):
                            try:
                                inner = json.loads(val)
                                if "translations" in inner:
                                    fixed_translations = inner["translations"]
                                    break
                            except (json.JSONDecodeError, TypeError):
                                pass
                if fixed_translations:
                    validated = TranslationResponse.model_validate(
                        {"translations": fixed_translations}
                    )
                else:
                    raise
            except (ValidationError, json.JSONDecodeError, Exception) as final_e:
                self.logger.error(f"All JSON parse attempts failed: {final_e}")
                self.logger.debug(f"Raw API response: {raw_content}")
                raise

        return validated

    # --- Translation Entry Points ---

    def _translate(self, src_list: List[str]) -> List[str]:
        if not src_list:
            return []

        RETRYABLE_EXCEPTIONS = (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
            openai.APIStatusError,
            httpx.RequestError,
        )

        translations = []
        to_lang = self.lang_map[self.lang_target]

        for prompt, num_src in self._assemble_prompts(src_list, to_lang):
            api_retry = 0
            mismatch_retry = 0

            while True:
                try:
                    parsed = self._request_translation(prompt)
                    if not parsed or not parsed.translations:
                        raise ValueError("Received empty response from API.")
                    if len(parsed.translations) != num_src:
                        raise ValueError(
                            f"Expected {num_src} translations, "
                            f"got {len(parsed.translations)}"
                        )
                    tr_dict = {
                        item.id: item.translation for item in parsed.translations
                    }
                    ordered = [tr_dict.get(i, "") for i in range(1, num_src + 1)]
                    translations.extend(ordered)
                    break

                except ValueError as e:
                    mismatch_retry += 1
                    self.logger.warning(
                        f"Translation structure mismatch: {e}. "
                        f"Attempt {mismatch_retry}/{self.invalid_repeat_count}."
                    )
                    if mismatch_retry >= self.invalid_repeat_count:
                        self.logger.error("Failed to get correct translation structure.")
                        translations.extend(["[ERROR: Structure Mismatch]"] * num_src)
                        break
                    time.sleep(self.retry_timeout / 2)

                except RETRYABLE_EXCEPTIONS as e:
                    api_retry += 1
                    self.logger.warning(
                        f"API Error ({type(e).__name__}): {e}. "
                        f"Attempt {api_retry}/{self.retry_attempts}."
                    )
                    if api_retry >= self.retry_attempts:
                        self.logger.error(
                            f"Failed after {self.retry_attempts} attempts."
                        )
                        translations.extend(["[ERROR: API Failed]"] * num_src)
                        break
                    time.sleep(self.retry_timeout)

        return translations

    # --- UI Integration ---

    def updateParam(self, param_key: str, param_content):
        if param_key == "manage_profiles":
            self._open_profile_manager()
            self._refresh_active_profile_options()
            return
        super().updateParam(param_key, param_content)
        if param_key == "active_profile":
            self.client = None
        if param_key == "proxy":
            self.client = None
        if param_key in ["max_requests_per_minute", "delay"]:
            self.request_count_minute = 0
            self.minute_start_time = time.time()
            self.last_request_time = 0

    def _open_profile_manager(self):
        try:
            from PyQt6.QtWidgets import QApplication
            parent = QApplication.activeWindow()
        except ImportError:
            from PyQt5.QtWidgets import QApplication
            parent = QApplication.activeWindow()
        if parent is None:
            return
        previous_active = self.get_param_value("active_profile")
        dialog = ProfileManagerDialog(
            parent, self._profiles_data,
            on_changed=lambda: self._serialize_profiles(),
        )
        dialog.exec()
        self._refresh_active_profile_options()
        if previous_active and self._find_profile(previous_active):
            self.params["active_profile"]["value"] = previous_active
        else:
            names = self._get_profile_names()
            if names:
                self.params["active_profile"]["value"] = names[0]
        # Persist profile changes to disk immediately
        from utils.config import save_config, pcfg
        pcfg.module.translator_params[self.name] = self.params
        save_config()


class ProfileManagerDialog(QDialog):
    def __init__(self, parent, profiles_data: List[Dict], on_changed=None):
        super().__init__(parent)
        self._profiles = profiles_data
        self._on_changed = on_changed
        self._current_row = -1
        self.setWindowTitle(self.tr("Manage API Profiles"))
        self.setMinimumSize(620, 420)
        self._build_ui()

    def _is_builtin(self, row: int) -> bool:
        return 0 <= row < len(self._profiles) and self._profiles[row].get("builtin", False)

    def _save_current_form(self):
        """Write form fields into self._profiles[self._current_row] and persist."""
        row = self._current_row
        if row < 0 or row >= len(self._profiles):
            return
        p = self._profiles[row]
        name = self.name_edit.text().strip()
        if not name:
            return
        p["name"] = name
        p["api_host"] = self.host_edit.text().strip()
        p["api_key"] = self.key_edit.text().strip()
        p["model"] = self.model_edit.text().strip()
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
        try:
            p["max_tokens"] = int(self.maxtok_edit.text() or "4096")
        except ValueError:
            p["max_tokens"] = 4096
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
        if self._on_changed:
            self._on_changed()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        splitter = QSplitter(self)

        # Left: profile list (fixed width)
        left_widget = QWidget()
        left_widget.setFixedWidth(220)
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(2, 2, 2, 2)
        left_layout.addWidget(QLabel(self.tr("Saved Profiles:")))
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_select)
        left_layout.addWidget(self.list_widget, 1)

        # Add / Delete on same row
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        self.add_new_btn = QPushButton(self.tr("+ Add"))
        self.add_new_btn.clicked.connect(self._on_add_new)
        self.delete_btn = QPushButton(self.tr("Delete"))
        self.delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self.add_new_btn, 1)
        btn_row.addWidget(self.delete_btn, 1)
        left_layout.addLayout(btn_row)

        # Right: scrollable edit form
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(5, 5, 5, 5)

        # --- Basic fields ---
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
        self.temp_edit = QLineEdit()
        self.temp_edit.setPlaceholderText("0.1")
        self.topp_edit = QLineEdit()
        self.topp_edit.setPlaceholderText("1.0")
        self.maxtok_edit = QLineEdit()
        self.maxtok_edit.setPlaceholderText("4096")
        form.addRow(self.tr("Name:"), self.name_edit)
        form.addRow(self.tr("Host:"), self.host_edit)
        form.addRow(self.tr("API Key:"), self.key_edit)
        form.addRow(self.tr("Model:"), model_row)
        form.addRow(self.tr("Temperature:"), self.temp_edit)
        form.addRow(self.tr("Top P:"), self.topp_edit)
        form.addRow(self.tr("Max Tokens:"), self.maxtok_edit)
        right_layout.addLayout(form)

        # --- Advanced fields ---
        right_layout.addWidget(QLabel(self.tr("Advanced (optional):")))
        adv_form = QFormLayout()
        self.rf_combo = QComboBox()
        self.rf_combo.addItems(["json_object", "json_schema"])
        self.rf_combo.setCurrentText("json_object")
        self.prompt_template_edit = QTextEdit()
        self.prompt_template_edit.setPlaceholderText(
            self.tr("Translate to {to_lang}:\n{input_json}")
        )
        self.prompt_template_edit.setMinimumHeight(100)
        self.chat_samples_edit = QTextEdit()
        self.chat_samples_edit.setPlaceholderText(
            self.tr("{to_lang}-{from_lang}:\n    source:\n        - text1\n    target:\n        - trans1")
        )
        self.chat_samples_edit.setMinimumHeight(100)
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
        right_layout.addStretch()

        scroll.setWidget(right_widget)
        splitter.addWidget(left_widget)
        splitter.addWidget(scroll)
        splitter.setSizes([200, 480])
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
                     self.fp_edit, self.pp_edit, self.prompt_template_edit]:
            edit.clear()
        self.chat_samples_edit.clear()
        self.chat_samples_edit.setPlainText("")
        self.rf_combo.setCurrentText("json_object")

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
        # Create a placeholder entry immediately
        new_profile = {"name": ""}
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
        self.host_edit.setText(p.get("api_host", ""))
        self.key_edit.setText(p.get("api_key", ""))
        self.model_edit.setText(p.get("model", ""))
        self.temp_edit.setText(str(p.get("temperature", "0.1")))
        self.topp_edit.setText(str(p.get("top_p", "1.0")))
        self.maxtok_edit.setText(str(p.get("max_tokens", "4096")))
        self.rf_combo.setCurrentText(p.get("response_format", "json_object"))
        self.prompt_template_edit.setPlainText(p.get("prompt_template", ""))
        self.chat_samples_edit.setPlainText(p.get("chat_samples", ""))
        self.fp_edit.setText(str(p.get("frequency_penalty", "")))
        self.pp_edit.setText(str(p.get("presence_penalty", "")))
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
