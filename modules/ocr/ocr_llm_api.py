import time
import base64
import json
import cv2
import numpy as np
from typing import List, Optional

import openai
import httpx

from .base import register_OCR, OCRBase, TextBlock


@register_OCR("llm_ocr")
class LLM_OCR(OCRBase):
    lang_map = {
        "Auto Detect": None,
        "Afrikaans": "af",
        "Albanian": "sq",
        "Amharic": "am",
        "Arabic": "ar",
        "Armenian": "hy",
        "Assamese": "as",
        "Azerbaijani": "az",
        "Bangla": "bn",
        "Basque": "eu",
        "Belarusian": "be",
        "Bengali": "bn",
        "Bosnian": "bs",
        "Breton": "br",
        "Bulgarian": "bg",
        "Burmese": "my",
        "Catalan": "ca",
        "Cebuano": "ceb",
        "Cherokee": "chr",
        "Chinese (Simplified)": "zh-CN",
        "Chinese (Traditional)": "zh-TW",
        "Corsican": "co",
        "Croatian": "hr",
        "Czech": "cs",
        "Danish": "da",
        "Dutch": "nl",
        "English": "en",
        "Esperanto": "eo",
        "Estonian": "et",
        "Faroese": "fo",
        "Filipino": "fil",
        "Finnish": "fi",
        "French": "fr",
        "Frisian": "fy",
        "Galician": "gl",
        "Georgian": "ka",
        "German": "de",
        "Greek": "el",
        "Gujarati": "gu",
        "Haitian Creole": "ht",
        "Hausa": "ha",
        "Hawaiian": "haw",
        "Hebrew": "he",
        "Hindi": "hi",
        "Hmong": "hmn",
        "Hungarian": "hu",
        "Icelandic": "is",
        "Igbo": "ig",
        "Indonesian": "id",
        "Interlingua": "ia",
        "Irish": "ga",
        "Italian": "it",
        "Japanese": "ja",
        "Javanese": "jv",
        "Kannada": "kn",
        "Kazakh": "kk",
        "Khmer": "km",
        "Korean": "ko",
        "Kurdish": "ku",
        "Kyrgyz": "ky",
        "Lao": "lo",
        "Latin": "la",
        "Latvian": "lv",
        "Lithuanian": "lt",
        "Luxembourgish": "lb",
        "Macedonian": "mk",
        "Malagasy": "mg",
        "Malay": "ms",
        "Malayalam": "ml",
        "Maltese": "mt",
        "Maori": "mi",
        "Marathi": "mr",
        "Mongolian": "mn",
        "Nepali": "ne",
        "Norwegian": "no",
        "Occitan": "oc",
        "Oriya": "or",
        "Pashto": "ps",
        "Persian": "fa",
        "Polish": "pl",
        "Portuguese": "pt",
        "Punjabi": "pa",
        "Quechua": "qu",
        "Romanian": "ro",
        "Russian": "ru",
        "Samoan": "sm",
        "Scots Gaelic": "gd",
        "Serbian (Cyrillic)": "sr-Cyrl",
        "Serbian (Latin)": "sr-Latn",
        "Shona": "sn",
        "Sindhi": "sd",
        "Sinhala": "si",
        "Slovak": "sk",
        "Slovenian": "sl",
        "Somali": "so",
        "Spanish": "es",
        "Sundanese": "su",
        "Swahili": "sw",
        "Swedish": "sv",
        "Tagalog": "tl",
        "Tajik": "tg",
        "Tamil": "ta",
        "Tatar": "tt",
        "Telugu": "te",
        "Thai": "th",
        "Tibetan": "bo",
        "Tigrinya": "ti",
        "Tongan": "to",
        "Turkish": "tr",
        "Ukrainian": "uk",
        "Urdu": "ur",
        "Uyghur": "ug",
        "Uzbek": "uz",
        "Vietnamese": "vi",
        "Welsh": "cy",
        "Xhosa": "xh",
        "Yiddish": "yi",
        "Yoruba": "yo",
        "Zulu": "zu",
    }

    params = {
        "profile": {
            "type": "selector",
            "options": [],
            "value": "",
            "description": "Select a vision-capable API profile. Manage profiles in Model Management.",
        },
        "language": {
            "type": "selector",
            "options": list(lang_map.keys()),
            "value": "Japanese",
            "description": "Language for OCR.",
        },
        "description": "OCR using various vision-capable LLMs configured via API profiles.",
    }

    def __init__(self, **params) -> None:
        super().__init__(**params)
        self._load_vision_profiles()
        self.last_request_time = 0
        self.client = None
        self.request_count_minute = 0
        self.minute_start_time = time.time()
        self.key_usage = {}
        self.current_key_index = 0

    # ── Profile Access ─────────────────────────────────────────────

    def _load_vision_profiles(self):
        """Refresh the profile selector options from shared storage."""
        from utils.profile_manager import get_vision_profile_names, load_profiles
        self._all_profiles = load_profiles()
        names = get_vision_profile_names()
        self.params["profile"]["options"] = names
        # Reset selection if current value no longer valid
        current = self.params["profile"]["value"]
        if current and current not in names:
            self.params["profile"]["value"] = names[0] if names else ""
        elif not current and names:
            self.params["profile"]["value"] = names[0]

    def _get_active_profile(self) -> dict:
        name = self.get_param_value("profile")
        if not name:
            return {}
        from utils.profile_manager import find_profile
        return find_profile(name) or {}

    # ── Connection helpers ─────────────────────────────────────────

    @property
    def _effective_api_host(self) -> str:
        return (self._get_active_profile().get("api_host") or "").strip()

    @property
    def _effective_api_key(self) -> str:
        return self._get_active_profile().get("api_key") or ""

    @property
    def _effective_model(self) -> str:
        return self._get_active_profile().get("model") or ""

    @property
    def _is_local_endpoint(self) -> bool:
        host = self._effective_api_host
        return bool(host and ("localhost" in host or "127.0.0.1" in host))

    @property
    def proxy(self) -> str:
        return self._get_active_profile().get("proxy") or ""

    @property
    def requests_per_minute(self) -> int:
        try:
            return int(self._get_active_profile().get("requests_per_minute", 0))
        except (ValueError, TypeError):
            return 0

    @property
    def request_delay(self) -> float:
        try:
            return float(self._get_active_profile().get("delay", 1.0))
        except (ValueError, TypeError):
            return 1.0

    @property
    def prompt(self) -> str:
        return self._get_active_profile().get("ocr_prompt", "")

    @property
    def system_prompt(self) -> str:
        return self._get_active_profile().get("ocr_system_prompt", "")

    @property
    def detail_level(self) -> str:
        return self._get_active_profile().get("ocr_detail_level", "auto")

    @property
    def max_response_tokens(self) -> int:
        try:
            return int(self._get_active_profile().get("ocr_max_response_tokens", 4096))
        except (ValueError, TypeError):
            return 4096

    # ── Key Management ─────────────────────────────────────────────

    @property
    def multiple_keys_list(self) -> List[str]:
        keys_str = self._get_active_profile().get("multiple_keys", "")
        if not isinstance(keys_str, str):
            return []
        return [
            key.strip()
            for key in keys_str.strip().replace("\n", ";").split(";")
            if key.strip()
        ]

    def _respect_key_limit(self, key: str) -> bool:
        rpm = self.requests_per_minute
        if rpm <= 0:
            return True
        now = time.time()
        count, start_time = self.key_usage.get(key, (0, now))
        if now - start_time >= 60:
            count, start_time = 0, now
        if count >= rpm:
            wait_time = 60.1 - (now - start_time)
            if wait_time > 0:
                self.logger.warning(
                    f"RPM limit ({rpm}) for key {key[:6]}... reached. Waiting {wait_time:.2f}s."
                )
                time.sleep(wait_time)
            self.key_usage[key] = (0, time.time())
            return False
        return True

    def _select_api_key(self) -> Optional[str]:
        api_keys = self.multiple_keys_list
        single_key = self._effective_api_key
        if not api_keys and not single_key:
            if self._is_local_endpoint:
                return "dummy-key"
            self.logger.error("No API keys provided.")
            return None

        if not api_keys:
            if self._respect_key_limit(single_key):
                now = time.time()
                count, start_time = self.key_usage.get(single_key, (0, now))
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
        self.logger.error("All API keys are rate-limited.")
        return None

    # ── Rate Limiting ──────────────────────────────────────────────

    def _respect_delay(self):
        current_time = time.time()
        rpm = self.requests_per_minute
        if rpm > 0:
            if current_time - self.minute_start_time >= 60:
                self.request_count_minute = 0
                self.minute_start_time = current_time
            if self.request_count_minute >= rpm:
                wait_time = 60.1 - (current_time - self.minute_start_time)
                if wait_time > 0:
                    self.logger.warning(
                        f"Global RPM limit ({rpm}) reached. Waiting {wait_time:.2f}s."
                    )
                    time.sleep(wait_time)
                self.request_count_minute = 0
                self.minute_start_time = time.time()

        time_since_last_request = current_time - self.last_request_time
        if time_since_last_request < self.request_delay:
            sleep_time = self.request_delay - time_since_last_request
            if self.debug_mode:
                self.logger.debug(f"Global delay: Waiting {sleep_time:.3f}s.")
            time.sleep(sleep_time)

        self.last_request_time = time.time()
        self.request_count_minute += 1

    # ── Client Initialization ──────────────────────────────────────

    def _initialize_client(self, api_key_to_use: str):
        endpoint = self._effective_api_host
        if not endpoint:
            self.logger.error("No api_host configured in the selected profile.")
            return

        http_client = None
        if self.proxy:
            try:
                proxy_mounts = {"all://": httpx.HTTPTransport(proxy=self.proxy)}
                http_client = httpx.Client(mounts=proxy_mounts)
            except Exception as e:
                self.logger.error(f"Failed to initialize proxy '{self.proxy}': {e}.")

        masked_key = (
            api_key_to_use[:4] + "..." + api_key_to_use[-4:]
            if len(api_key_to_use) > 8
            else api_key_to_use
        )
        self.logger.debug(
            f"Initializing client with key {masked_key} at endpoint {endpoint}"
        )

        self.client = openai.OpenAI(
            api_key=api_key_to_use, base_url=endpoint, http_client=http_client
        )

    # ── OCR ────────────────────────────────────────────────────────

    def ocr(self, img_base64: str, prompt_override: str = None) -> str:
        profile = self._get_active_profile()
        if not profile:
            return "[ERROR: No profile selected. Select a vision-capable profile in settings.]"

        api_key_to_use = self._select_api_key()
        if not api_key_to_use:
            return "[ERROR: No available API key]"

        if not self.client or self.client.api_key != api_key_to_use:
            self._initialize_client(api_key_to_use)

        self._respect_delay()
        try:
            lang_name = self.get_param_value("language")
            prompt_text = (prompt_override or self.prompt).format(language=lang_name)

            image_content_part = {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img_base64}"},
            }

            detail = self.detail_level
            if detail in ("low", "high"):
                image_content_part["image_url"]["detail"] = detail

            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        image_content_part,
                    ],
                }
            ]
            if self.system_prompt:
                messages.insert(0, {"role": "system", "content": self.system_prompt})

            model_name = self._effective_model
            self.logger.debug(f"OCR request with model: {model_name}")

            response = self.client.chat.completions.create(
                model=model_name,
                messages=messages,
                max_tokens=self.max_response_tokens,
            )

            if response.choices and response.choices[0].message.content:
                full_text = (
                    response.choices[0].message.content.replace("\n", " ").strip()
                )
                self.logger.debug(f"OCR result: {full_text}")
                return full_text
            else:
                self.logger.warning("No text found in OCR response.")
                return ""
        except Exception as e:
            self.logger.error(f"OCR error: {e}")
            return f"[ERROR: {type(e).__name__}]"

    def _ocr_blk_list(
        self, img: np.ndarray, blk_list: List[TextBlock], *args, **kwargs
    ):
        im_h, im_w = img.shape[:2]
        for blk in blk_list:
            x1, y1, x2, y2 = blk.xyxy
            if 0 <= x1 < x2 <= im_w and 0 <= y1 < y2 <= im_h:
                cropped_img = img[y1:y2, x1:x2]
                _, buffer = cv2.imencode(".jpg", cropped_img)
                img_base64 = base64.b64encode(buffer).decode("utf-8")
                blk.text = self.ocr(img_base64, prompt_override=kwargs.get("prompt"))
            else:
                blk.text = ""

    def ocr_img(self, img: np.ndarray, prompt: str = "") -> str:
        _, buffer = cv2.imencode(".jpg", img)
        img_base64 = base64.b64encode(buffer).decode("utf-8")
        return self.ocr(img_base64, prompt_override=prompt)

    def updateParam(self, param_key: str, param_content):
        super().updateParam(param_key, param_content)
        if param_key == "profile":
            self.client = None
            self.request_count_minute = 0
            self.minute_start_time = time.time()
            self.last_request_time = 0
