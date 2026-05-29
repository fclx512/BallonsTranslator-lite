import json
import re
import time
from typing import Dict, List, Optional

import httpx
import openai
from pydantic import BaseModel, Field, ValidationError

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


@register_translator("LLM_API_Translator")
class LLM_API_Translator(BaseTranslator):
    concate_text = False
    cht_require_convert = True

    params = {
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
        self._src_lang_map = {"Auto Detect": "Auto", **self.lang_map}
        self._load_profiles_from_shared()
        self._refresh_active_profile_options()
        # Sync profiles to global config so AI chat panel can read them
        from utils.config import pcfg as _pcfg
        if self.name not in _pcfg.module.translator_params:
            _pcfg.module.translator_params[self.name] = {}
        _pcfg.module.translator_params[self.name].update(self.params)

    @property
    def supported_src_list(self):
        return ["Auto Detect"] + self.valid_lang_list

    @property
    def supported_tgt_list(self):
        return self.valid_lang_list

    # --- Profile Access (shared storage) ---

    def _load_profiles_from_shared(self):
        """Load profiles from the shared profile_manager."""
        from utils.profile_manager import load_profiles
        self._profiles_data = load_profiles()

    def _refresh_active_profile_options(self):
        from utils.profile_manager import get_profile_names
        names = get_profile_names()
        self.params["active_profile"]["options"] = names
        if names and not self.params["active_profile"].get("value"):
            self.params["active_profile"]["value"] = names[0]

    def _get_profile_names(self) -> List[str]:
        from utils.profile_manager import get_profile_names
        return get_profile_names()

    def _find_profile(self, name: str) -> Optional[Dict]:
        from utils.profile_manager import find_profile
        return find_profile(name)

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
    def max_tokens(self) -> Optional[int]:
        val = self._active_profile.get("max_tokens", "")
        if not val:
            return None
        try:
            v = int(val)
            return v if v > 0 else None
        except (ValueError, TypeError):
            return None

    @property
    def max_rpm(self) -> int:
        # Check profile first, fall back to top-level param
        profile_val = self._active_profile.get("requests_per_minute")
        if profile_val is not None:
            try:
                return int(profile_val)
            except (ValueError, TypeError):
                pass
        return int(self.get_param_value("max_requests_per_minute"))

    @property
    def global_delay(self) -> float:
        # Check profile first, fall back to top-level param
        profile_val = self._active_profile.get("delay")
        if profile_val is not None:
            try:
                return float(profile_val)
            except (ValueError, TypeError):
                pass
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
        # Check profile first, fall back to top-level param
        profile_val = self._active_profile.get("proxy")
        if profile_val:
            return profile_val
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
        from_lang = self._src_lang_map.get(self.lang_source, self.lang_source)
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
        }
        if self.max_tokens is not None:
            api_args["max_tokens"] = self.max_tokens
        rf = profile.get("response_format", "")
        if rf == "json_schema":
            api_args["response_format"] = {
                "type": "json_schema",
                "json_schema": {"schema": TranslationResponse.model_json_schema()},
            }
        elif self._is_local_endpoint:
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
        super().updateParam(param_key, param_content)
        if param_key == "active_profile":
            self.client = None
        if param_key == "proxy":
            self.client = None
        if param_key in ["max_requests_per_minute", "delay"]:
            self.request_count_minute = 0
            self.minute_start_time = time.time()
            self.last_request_time = 0

