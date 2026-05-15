import json
import re
import time
import traceback
from typing import List, Dict, Optional

import httpx
import openai
import yaml
from pydantic import BaseModel, Field, ValidationError

from .base import BaseTranslator, register_translator


class TranslationElement(BaseModel):
    id: int = Field(..., description="The original ID of the text snippet.")
    translation: str = Field(..., description="The translated text.")


class TranslationResponse(BaseModel):
    translations: List[TranslationElement] = Field(
        ..., description="List of translated elements."
    )


@register_translator("ChatGPT")
class GPTTranslator(BaseTranslator):
    concate_text = False
    cht_require_convert = True

    params: Dict = {
        "api key": {
            "value": "",
            "description": "API 密钥，从 OpenAI 或其他兼容服务商获取",
        },
        "multiple_keys": {
            "type": "editor",
            "value": "",
            "description": "多个 API 密钥，用分号(;)分隔。多个 key 会自动轮换使用，提高请求频率上限",
        },
        "model": {
            "type": "selector",
            "options": [
                "gpt-4o",
                "gpt-4o-mini",
                "gpt-4-turbo",
                "gpt-3.5-turbo",
            ],
            "value": "gpt-4o",
            "editable": True,
            "flush_btn": True,
            "description": "选择模型。点击右侧 Refresh 按钮可从 API 在线获取最新模型列表",
        },
        "override model": {
            "value": "",
            "description": "自定义模型名称，优先级高于下拉框选择。用于使用列表中没有的模型",
        },
        "prompt template": {
            "type": "editor",
            "value": "Please help me to translate the following text from a manga to {to_lang} (if it's already in {to_lang} or looks like gibberish you have to output it as it is instead):\n",
            "description": "翻译提示模板。{to_lang} 会被替换为目标语言。建议保留默认模板",
        },
        "chat system template": {
            "type": "editor",
            "value": "You are a professional translation engine, please translate the text into a colloquial, elegant and fluent content, without referencing machine translations. You must only translate the text content, never interpret it. If there's any issue in the text, output the text as is.\nTranslate to {to_lang}.",
            "description": "系统提示词，用于设定翻译助手的角色和行为",
        },
        "chat sample": {
            "type": "editor",
            "value": (
                "日本語-简体中文:\n"
                "    source:\n"
                "        - 二人のちゅーを 目撃した ぼっちちゃん\n"
                "        - ふたりさん\n"
                "        - 大好きなお友達には あいさつ代わりに ちゅーするんだって\n"
                "        - アイス あげた\n"
                "        - 喜多ちゃんとは どどど どういった ご関係なのでしようか...\n"
                "        - テレビで見た！\n"
                "    target:\n"
                "        - 小孤独目击了两人的接吻\n"
                "        - 二里酱\n"
                "        - 我听说人们会把亲吻作为与喜爱的朋友打招呼的方式\n"
                "        - 我给了她冰激凌\n"
                "        - 喜多酱和你是怎么样的关系啊...\n"
                "        - 我在电视上看到的！"
            ),
            "description": '少样本翻译示例，YAML 格式。按 "源语言-目标语言" 分组，帮助模型理解翻译风格',
        },
        "max requests per minute": {
            "value": 20,
            "description": "每分钟最大请求数，超出后会自动等待。根据 API 套餐限制调整",
        },
        "delay": {
            "value": 0.3,
            "description": "每次请求间的延迟（秒），避免触发频率限制",
        },
        "max tokens": {
            "value": 4096,
            "description": "每次请求的最大 Token 数，控制回复长度",
        },
        "temperature": {
            "value": 0.5,
            "description": "采样温度(0-2)。越低越确定，越高越随机。翻译推荐 0.3-0.7",
        },
        "top p": {
            "value": 1.0,
            "description": "核采样参数，一般保持默认 1.0",
        },
        "retry attempts": {
            "value": 5,
            "description": "请求失败后的最大重试次数",
        },
        "retry timeout": {
            "value": 15,
            "description": "重试等待时间（秒）",
        },
        "frequency penalty": {
            "value": 0.0,
            "description": "频率惩罚(OpenAI)。正值减少重复用词",
        },
        "presence penalty": {
            "value": 0.0,
            "description": "存在惩罚(OpenAI)。正值鼓励谈论新话题",
        },
        "proxy": {
            "value": "",
            "description": "代理地址，例如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080",
        },
        "invalid repeat count": {
            "value": 2,
            "description": "翻译数量不匹配时的重试次数",
        },
        "3rd party api url": {
            "value": "",
            "description": "第三方 API 地址。留空使用 OpenAI 官方地址。需包含 /v1 路径",
        },
        "low vram mode": {
            "value": False,
            "type": "checkbox",
            "description": "本地运行且 VRAM 不足时启用，可减少显存占用",
        },
    }

    def _setup_translator(self):
        self.lang_map = {
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

    # --- Properties ---

    @property
    def model(self) -> str:
        return self.get_param_value("model")

    @property
    def override_model(self) -> Optional[str]:
        return self.get_param_value("override model") or None

    @property
    def resolved_model(self) -> str:
        return self.override_model or self.model

    @property
    def temperature(self) -> float:
        return float(self.get_param_value("temperature"))

    @property
    def max_tokens(self) -> int:
        return int(self.get_param_value("max tokens"))

    @property
    def top_p(self) -> float:
        return float(self.get_param_value("top p"))

    @property
    def retry_attempts(self) -> int:
        return int(self.get_param_value("retry attempts"))

    @property
    def retry_timeout(self) -> float:
        return float(self.get_param_value("retry timeout"))

    @property
    def invalid_repeat_count(self) -> int:
        return int(self.get_param_value("invalid repeat count"))

    @property
    def max_rpm(self) -> int:
        return int(self.get_param_value("max requests per minute"))

    @property
    def global_delay(self) -> float:
        return float(self.get_param_value("delay"))

    @property
    def frequency_penalty(self) -> float:
        return float(self.get_param_value("frequency penalty"))

    @property
    def presence_penalty(self) -> float:
        return float(self.get_param_value("presence penalty"))

    @property
    def apikey(self) -> str:
        return self.get_param_value("api key")

    @property
    def multiple_keys_list(self) -> List[str]:
        keys_str = self.get_param_value("multiple_keys")
        if not isinstance(keys_str, str):
            return []
        return [
            k.strip()
            for k in keys_str.strip().replace("\n", ";").split(";")
            if k.strip()
        ]

    def delay(self) -> float:
        return self.global_delay

    # --- System Prompt ---

    @property
    def system_prompt(self) -> str:
        to_lang = self.lang_map[self.lang_target]
        template = self.get_param_value("chat system template")
        prompt = template.format(to_lang=to_lang)
        prompt += (
            '\n\nYou must respond with a JSON object in exactly this format: '
            '{"translations": [{"id": <number>, "translation": "<translated text>"}]}'
        )
        return prompt

    # --- Chat Sample ---

    @property
    def chat_sample(self) -> List[Dict]:
        """Returns [user_msg, assistant_msg] for few-shot learning, or empty list."""
        samples_text = self.get_param_value("chat sample")
        if not samples_text:
            return []

        try:
            samples = yaml.load(samples_text, Loader=yaml.FullLoader)
        except Exception as e:
            self.logger.error(f"Failed to parse chat sample: {e}")
            return []

        src_tgt = f"{self.lang_source}-{self.lang_target}"
        if src_tgt not in samples:
            return []

        src_list = samples[src_tgt].get("source", [])
        tgt_list = samples[src_tgt].get("target", [])
        if not src_list or not tgt_list:
            return []

        input_elements = [
            {"id": i + 1, "source": s} for i, s in enumerate(src_list)
        ]
        output_elements = [
            {"id": i + 1, "translation": t} for i, t in enumerate(tgt_list)
        ]

        return [
            {
                "role": "user",
                "content": json.dumps(input_elements, ensure_ascii=False),
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    {"translations": output_elements}, ensure_ascii=False
                ),
            },
        ]

    # --- API URL ---

    @property
    def api_url(self) -> Optional[str]:
        url = self.get_param_value("3rd party api url").strip()
        if not url:
            return None
        if "/v1" not in url:
            self.logger.warning(
                f"API URL does not contain '/v1': {url}, please ensure it's correct."
            )
        return url.rstrip("/")

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
                    f"RPM limit ({rpm}) reached for key ...{key[-4:]}. Waiting {wait_time:.1f}s."
                )
                time.sleep(wait_time)
            self.key_usage[key] = (0, time.time())
            return False
        return True

    def _select_api_key(self) -> Optional[str]:
        api_keys = self.multiple_keys_list
        single_key = self.apikey

        if not api_keys and not single_key:
            self.logger.error("No API keys provided in parameters.")
            return None

        if not api_keys:
            if self._respect_key_limit(single_key):
                now = time.time()
                count, start_time = self.key_usage.get(single_key, (0, now))
                if now - start_time >= 60:
                    count = 0
                    start_time = now
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

        self.logger.error("All available API keys are currently rate-limited.")
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
                    self.logger.warning(
                        f"Global RPM limit ({rpm}) reached. Waiting {wait:.1f}s."
                    )
                    time.sleep(wait)
                self.request_count_minute = 0
                self.minute_start_time = time.time()

        elapsed = now - self.last_request_time
        if elapsed < delay:
            time.sleep(delay - elapsed)

        self.last_request_time = time.time()
        self.request_count_minute += 1

    # --- Client Initialization ---

    def _initialize_client(self, api_key: str) -> bool:
        endpoint = self.api_url or "https://api.openai.com/v1"
        proxy = self.get_param_value("proxy")

        http_client = None
        if proxy:
            try:
                http_client = httpx.Client(proxies=proxy)
            except Exception as e:
                self.logger.error(f"Failed to initialize proxy '{proxy}': {e}")

        try:
            self.client = openai.OpenAI(
                api_key=api_key,
                base_url=endpoint,
                http_client=http_client,
            )
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize OpenAI client: {e}")
            self.client = None
            return False

    # --- Model List Fetching ---

    def fetch_models(self) -> List[str]:
        api_key = self._select_api_key()
        if not api_key:
            return []

        endpoint = self.api_url or "https://api.openai.com/v1"

        try:
            proxy = self.get_param_value("proxy")
            client_kwargs = {}
            if proxy:
                client_kwargs["proxies"] = proxy

            with httpx.Client(**client_kwargs) as client:
                resp = client.get(
                    f"{endpoint}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    models = resp.json().get("data", [])
                    return sorted(
                        m["id"]
                        for m in models
                        if m["id"].startswith(("gpt", "o"))
                    )
                else:
                    self.logger.warning(
                        f"Failed to fetch models: HTTP {resp.status_code}"
                    )
        except Exception as e:
            self.logger.warning(f"Failed to fetch model list: {e}")
        return []

    def flush(self, param_key: str):
        if param_key == "model":
            models = self.fetch_models()
            if models:
                self.params["model"]["options"] = models
                return models
            return []  # Return empty list instead of None to avoid crash
        return None

    # --- Prompt Assembly ---

    def _assemble_prompts(
        self,
        queries: List[str],
        from_lang: str = None,
        to_lang: str = None,
        max_tokens: int = None,
    ):
        if from_lang is None:
            from_lang = self.lang_map[self.lang_source]
        if to_lang is None:
            to_lang = self.lang_map[self.lang_target]

        input_elements = [
            {"id": i + 1, "source": q} for i, q in enumerate(queries)
        ]
        input_json = json.dumps(input_elements, ensure_ascii=False, indent=2)

        template = (
            self.get_param_value("prompt template").format(to_lang=to_lang).rstrip()
        )
        prompt = f"{template}\n\n{input_json}"
        yield prompt, len(queries)

    # --- Core Translation ---

    def _request_translation(self, prompt: str) -> Optional[TranslationResponse]:
        current_api_key = self._select_api_key()
        if not current_api_key:
            raise ConnectionError("No available API key found.")

        if not self._initialize_client(current_api_key):
            raise ConnectionError("Failed to initialize API client.")

        self._respect_delay()

        model_name = self.resolved_model
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self.chat_sample)
        messages.append({"role": "user", "content": prompt})

        api_args = {
            "model": model_name,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "response_format": {"type": "json_object"},
        }
        if self.frequency_penalty:
            api_args["frequency_penalty"] = self.frequency_penalty
        if self.presence_penalty:
            api_args["presence_penalty"] = self.presence_penalty

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

        raw_content = completion.choices[0].message.content
        if not raw_content:
            self.logger.warning("No valid message content in API response.")
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
                f"Pydantic validation failed: {e}. Attempting to fix simple format."
            )
            try:
                data = json.loads(json_str)
                if isinstance(data, dict) and all(
                    k.isdigit() for k in data.keys()
                ):
                    fixed = [
                        {"id": int(k), "translation": v}
                        for k, v in data.items()
                    ]
                    validated = TranslationResponse.model_validate(
                        {"translations": fixed}
                    )
                elif isinstance(data, list):
                    validated = TranslationResponse.model_validate(
                        {"translations": data}
                    )
                else:
                    raise
            except (ValidationError, json.JSONDecodeError, Exception) as final_e:
                self.logger.error(
                    f"All JSON parse attempts failed: {final_e}"
                )
                self.logger.debug(f"Raw API response: {raw_content}")
                raise

        return validated

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
        from_lang = self.lang_map[self.lang_source]
        to_lang = self.lang_map[self.lang_target]

        for prompt, num_src in self._assemble_prompts(src_list, from_lang, to_lang):
            api_retry = 0
            mismatch_retry = 0

            while True:
                try:
                    parsed = self._request_translation(prompt)

                    if not parsed or not parsed.translations:
                        raise ValueError(
                            "Received empty or invalid parsed response from API."
                        )

                    if len(parsed.translations) != num_src:
                        raise ValueError(
                            f"Expected {num_src} translations, got {len(parsed.translations)}"
                        )

                    tr_dict = {
                        item.id: item.translation for item in parsed.translations
                    }
                    ordered = [tr_dict.get(i, "") for i in range(1, num_src + 1)]
                    translations.extend(ordered)
                    self.logger.info(
                        f"Successfully translated batch of {num_src}. Tokens used: {self.token_count_last}"
                    )
                    break

                except ValueError as e:
                    mismatch_retry += 1
                    self.logger.warning(
                        f"Translation structure mismatch: {e}. "
                        f"Attempt {mismatch_retry}/{self.invalid_repeat_count}."
                    )
                    if mismatch_retry >= self.invalid_repeat_count:
                        self.logger.error(
                            "Fatal Error: Failed to get correct translation structure after retries."
                        )
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
                            f"Fatal Error: Failed to connect to API after {self.retry_attempts} attempts."
                        )
                        translations.extend(["[ERROR: API Failed]"] * num_src)
                        break
                    time.sleep(self.retry_timeout)

                except (
                    openai.BadRequestError,
                    openai.AuthenticationError,
                    openai.PermissionDeniedError,
                ) as e:
                    self.logger.error(
                        f"Fatal API error: {type(e).__name__}: {e}"
                    )
                    translations.extend([f"[ERROR: {type(e).__name__}]"] * num_src)
                    break

        if self.token_count_last:
            self.logger.info(
                f"Used {self.token_count_last} tokens (Total: {self.token_count})"
            )

        return translations

    def updateParam(self, param_key: str, param_content):
        super().updateParam(param_key, param_content)
        if param_key in {"proxy", "multiple_keys", "api key", "3rd party api url"}:
            self.client = None
