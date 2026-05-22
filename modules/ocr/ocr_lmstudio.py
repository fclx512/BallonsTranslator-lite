import time
import base64
import cv2
import numpy as np
from typing import List, Optional

import openai
import httpx

from .base import register_OCR, OCRBase, TextBlock


@register_OCR("lmstudio_ocr")
class LMStudioOCR(OCRBase):
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
        "endpoint": {
            "value": "http://localhost:1234/v1",
            "description": "LM Studio server base URL (default: http://localhost:1234/v1).",
        },
        "model": {
            "value": "",
            "description": "Vision model name loaded in LM Studio (e.g., qwen2-vl-7b, llava-v1.5-7b).",
        },
        "language": {
            "type": "selector",
            "options": list(lang_map.keys()),
            "value": "Japanese",
            "description": "Language for OCR.",
        },
        "prompt": {
            "type": "editor",
            "value": (
                "Perform OCR on the provided manga image snippet. The language is **{language}**.\n"
                "Recognize all text, including handwritten sound effects (SFX).\n"
                "**CRITICAL INSTRUCTION:** If you see jumbled characters, it is likely vertical text "
                "that was read horizontally. First, mentally reconstruct the correct vertical text.\n"
                "**OUTPUT FORMATTING:** All recognized text from the image must be consolidated "
                "into a **single, continuous horizontal line**. Do not use newlines.\n"
                "Your final output must be ONLY the recognized text. No explanations."
            ),
            "description": "The main prompt for the OCR task. Use {language} placeholder.",
        },
        "system_prompt": {
            "type": "editor",
            "value": (
                "You are a specialized OCR engine for manga and comics. "
                "Your primary function is to accurately extract and consolidate all recognized text "
                "from an image into a **single, continuous horizontal line**. "
                "You must return only the raw, recognized text. "
                "You do not interpret, translate, or explain the content."
            ),
            "description": "Optional system prompt to guide the model's behavior.",
        },
        "proxy": {
            "value": "",
            "description": "Proxy address (e.g. http(s)://user:password@host:port)",
        },
        "delay": {"value": 0.5, "description": "Delay in seconds between requests."},
        "max_response_tokens": {
            "value": 4096,
            "description": "Maximum number of tokens in the LLM's response.",
        },
        "description": "OCR using a locally running LM Studio vision model.",
    }

    def __init__(self, **params) -> None:
        super().__init__(**params)
        self.last_request_time = 0
        self.client = None

    def _initialize_client(self):
        endpoint = self.get_param_value("endpoint") or "http://localhost:1234/v1"
        proxy = self.get_param_value("proxy")

        http_client = None
        if proxy:
            try:
                proxy_mounts = {"all://": httpx.HTTPTransport(proxy=proxy)}
                http_client = httpx.Client(mounts=proxy_mounts)
            except Exception as e:
                self.logger.error(f"Failed to initialize proxy '{proxy}': {e}.")

        self.logger.debug(f"Initializing LM Studio client at endpoint {endpoint}")
        self.client = openai.OpenAI(
            api_key="dummy-key", base_url=endpoint, http_client=http_client
        )

    @property
    def model(self) -> str:
        return self.get_param_value("model")

    @property
    def language(self) -> str:
        return self.get_param_value("language")

    @property
    def request_delay(self) -> float:
        try:
            return float(self.get_param_value("delay"))
        except (ValueError, TypeError):
            return 0.5

    @property
    def max_response_tokens(self) -> int:
        return int(self.get_param_value("max_response_tokens"))

    def ocr(self, img_base64: str, prompt_override: str = None) -> str:
        if not self.client:
            self._initialize_client()

        model_name = self.model
        if not model_name:
            return "[ERROR: No model specified. Enter the vision model name in settings.]"

        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.request_delay:
            time.sleep(self.request_delay - time_since_last)
        self.last_request_time = time.time()

        try:
            lang_name = self.language
            prompt_text = (prompt_override or self.get_param_value("prompt")).format(
                language=lang_name
            )

            system_prompt = self.get_param_value("system_prompt")
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})

            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_base64}"},
                    },
                ],
            })

            self.logger.debug(f"LM Studio OCR request with model: {model_name}")
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
                self.logger.warning("No text found in LM Studio OCR response.")
                return ""
        except Exception as e:
            self.logger.error(f"LM Studio OCR error: {e}")
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
        if param_key in ["endpoint", "proxy"]:
            self.client = None
        if param_key == "delay":
            self.last_request_time = 0
