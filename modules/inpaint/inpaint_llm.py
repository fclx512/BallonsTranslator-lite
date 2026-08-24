import base64
import io
import time
from typing import Dict, List
from urllib.parse import urlparse, urlunparse

import cv2
import numpy as np
from PIL import Image

from .base import InpainterBase, register_inpainter
from ..textdetector import TextBlock

# Only highlight the mask as a repair marker while it covers a genuine partial
# region. If the mask covers nearly the whole crop there is no surrounding
# artwork to reconstruct the background from, so a highlight marker would only
# tint everything and help nothing.
_MASK_MIN_SUBSET = 0.90

# Short, focused repair prompt sent when a partial region is highlighted. It
# replaces the profile's long generic ``image_prompt`` so the model focuses on
# erasing the marked region instead of restyling the whole crop (which is what
# produced the "didn't erase text / added coloured edges" regression). A repair
# marker is an LLM prompt and is intentionally not translated.
_REGIONAL_PROMPT = (
    "The area highlighted in red is embedded text or damage that must be removed. "
    "Treat the red as a repair marker, not as image content, and reconstruct the "
    "background beneath it so it continues the surrounding artwork naturally. "
    "Keep every pixel outside the red area unchanged."
)


class LLMInpaintError(RuntimeError):
    """Non-retryable config/validation error for online LLM inpainting."""

    def __init__(self, message):
        super().__init__(message)


class LLMInpaintStopped(LLMInpaintError):
    """Inpainting was cancelled via the stop event."""


@register_inpainter("LLMInpaint")
class LLMInpaint(InpainterBase):
    """Profile-backed image cleanup using image-capable LLM APIs.

    The API endpoint, image model and API key are read from the shared LLM
    profile selected in the ``profile`` param (managed in Model Management).
    Three request flavours are auto-detected from the endpoint host:
    OpenAI-compatible, Gemini, and OpenRouter.
    """

    dependencies = ["httpx[socks,brotli]"]

    params: Dict = {
        "profile": {
            "type": "selector",
            "options": [],
            "value": "",
            "description": "Select an image-capable API profile. Manage profiles in Model Management.",
        },
        "max resolution": {
            "type": "selector",
            "options": [0, 256, 768, 1280],
            "value": 1280,
            "description": "Scale images down before sending them to the LLM. Set to 0 to keep the original size.",
        },
        "inpaint by block": {
            "type": "checkbox",
            "value": True,
            "description": "Send each text block crop separately instead of sending the whole image.",
        },
        "retry attempts": {
            "value": 3,
            "description": "Retries for API failures.",
        },
        "retry timeout": {
            "value": 7.0,
            "description": "Delay between retries in seconds.",
        },
        "request timeout": {
            "value": 180.0,
            "description": "HTTP timeout for image cleanup requests in seconds. Set to 0 to disable.",
        },
        "description": "Inpaint using the selected image-capable LLM profile.",
    }

    inpaint_by_block = True

    def __init__(self, **params) -> None:
        super().__init__(**params)
        self._sync_inpaint_by_block()
        self._load_inpaint_profiles()
        self.client = None
        self.client_cache_key = None
        self.last_request_time = 0
        self.request_count_minute = 0
        self.minute_start_time = time.time()
        self.stop_event = None

    # ── Profile Access ─────────────────────────────────────────────

    def _load_inpaint_profiles(self):
        """Refresh the profile selector options from shared storage."""
        from utils.profile_manager import get_image_profile_names

        names = get_image_profile_names()
        self.params["profile"]["options"] = names
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

    @property
    def _image_model(self) -> str:
        return (self._get_active_profile().get("image_model") or "").strip()

    @property
    def _image_prompt(self) -> str:
        return self._get_active_profile().get("image_prompt") or ""

    def _image_base_url(self, profile: dict) -> str:
        base_url = (profile.get("image_base_url") or "").strip()
        if not base_url:
            base_url = (profile.get("api_host") or "").strip()
        if not base_url:
            raise LLMInpaintError(
                f'No image endpoint configured for profile "{profile.get("name")}".'
            )
        return base_url

    @staticmethod
    def _is_local_endpoint(base_url: str) -> bool:
        host = urlparse(base_url).netloc.lower()
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
            return float(self._get_active_profile().get("delay", 0.5))
        except (ValueError, TypeError):
            return 0.5

    # ── Module lifecycle ──────────────────────────────────────────

    def _sync_inpaint_by_block(self):
        value = self.get_param_value("inpaint by block")
        if isinstance(value, str):
            value = value.lower().strip() == "true"
        self.inpaint_by_block = bool(value)

    def updateParam(self, param_key: str, param_content):
        super().updateParam(param_key, param_content)
        if param_key == "profile":
            self.client = None
            self.request_count_minute = 0
            self.minute_start_time = time.time()
            self.last_request_time = 0
        elif param_key == "inpaint by block":
            self._sync_inpaint_by_block()

    def set_stop_event(self, stop_event):
        self.stop_event = stop_event

    def _wait(self, seconds: float):
        if seconds <= 0:
            return
        if self.stop_event is not None:
            if self.stop_event.wait(seconds):
                raise LLMInpaintStopped()
            return
        time.sleep(seconds)

    def _request_timeout(self):
        try:
            timeout = float(self.get_param_value("request timeout") or 0)
        except (TypeError, ValueError):
            timeout = 180.0
        return None if timeout <= 0 else timeout

    def _max_resolution(self) -> int:
        try:
            return int(self.get_param_value("max resolution") or 0)
        except (TypeError, ValueError):
            return 1280

    # ── HTTP helpers ──────────────────────────────────────────────

    def _scale_image_for_request(self, img: np.ndarray) -> np.ndarray:
        max_resolution = self._max_resolution()
        if max_resolution <= 0:
            return img
        height, width = img.shape[:2]
        long_side = max(height, width)
        if long_side <= max_resolution:
            return img
        scale = max_resolution / long_side
        new_size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
        return cv2.resize(img, new_size, interpolation=cv2.INTER_AREA)

    def _http_client(self, proxy: str):
        import httpx  # type: ignore

        client_kwargs = {"timeout": self._request_timeout()}
        if not proxy:
            return httpx.Client(**client_kwargs)
        try:
            mounts = {
                "http://": httpx.HTTPTransport(proxy=proxy),
                "https://": httpx.HTTPTransport(proxy=proxy),
            }
            return httpx.Client(mounts=mounts, **client_kwargs)
        except Exception as e:
            self.logger.error(
                f"Failed to initialize proxy '{proxy}': {e}. Proceeding without proxy."
            )
            return httpx.Client(**client_kwargs)

    def _api_key_for_profile(self, profile: dict) -> str:
        api_key = (profile.get("api_key") or "").strip()
        if not api_key:
            if self._is_local_endpoint(self._image_base_url(profile)):
                return "dummy-key"
            raise LLMInpaintError(
                f'API key is required for profile "{profile.get("name")}".'
            )
        return api_key

    def _initialize_client(self, profile: dict):
        api_key = self._api_key_for_profile(profile)
        base_url = self._image_base_url(profile)
        proxy = self.proxy
        request_timeout = self._request_timeout()
        cache_key = (api_key, base_url, proxy, request_timeout)
        if self.client is not None and self.client_cache_key == cache_key:
            return self.client

        self.client = self._http_client(proxy)
        self.client_cache_key = cache_key
        return self.client

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
                        f"Global RPM limit ({rpm}) reached. Waiting {wait_time:.2f} seconds."
                    )
                    self._wait(wait_time)
                self.request_count_minute = 0
                self.minute_start_time = time.time()

        time_since_last_request = current_time - self.last_request_time
        if time_since_last_request < self.request_delay:
            self._wait(self.request_delay - time_since_last_request)

        self.last_request_time = time.time()
        self.request_count_minute += 1

    # ── Response parsing ──────────────────────────────────────────

    @staticmethod
    def _response_field(item, field_name: str):
        if isinstance(item, dict):
            return item.get(field_name)
        return getattr(item, field_name, None)

    def _decode_image_bytes(self, raw: bytes) -> np.ndarray:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        return np.array(image)

    def _download_image(self, url: str) -> np.ndarray:
        proxy = self.proxy
        client = self._http_client(proxy)
        try:
            response = client.get(url)
            response.raise_for_status()
            return self._decode_image_bytes(response.content)
        finally:
            client.close()

    def _decode_response_image(self, response) -> np.ndarray:
        data = self._response_field(response, "data")
        if not data:
            raise RuntimeError("LLM image cleanup returned no image data.")

        item = data[0]
        b64_json = self._response_field(item, "b64_json")
        if b64_json:
            return self._decode_image_bytes(base64.b64decode(b64_json))

        url = self._response_field(item, "url")
        if url:
            return self._download_image(str(url))

        raise RuntimeError("LLM image cleanup returned no decodable image.")

    def _decode_gemini_response_image(self, response) -> np.ndarray:
        candidates = self._response_field(response, "candidates") or []
        for candidate in candidates:
            content = self._response_field(candidate, "content") or {}
            for part in self._response_field(content, "parts") or []:
                inline_data = (
                    self._response_field(part, "inline_data")
                    or self._response_field(part, "inlineData")
                )
                data = self._response_field(inline_data, "data") if inline_data else None
                if data:
                    return self._decode_image_bytes(base64.b64decode(str(data)))

        output_image = (
            self._response_field(response, "output_image")
            or self._response_field(response, "outputImage")
        )
        data = self._response_field(output_image, "data") if output_image else None
        if data:
            return self._decode_image_bytes(base64.b64decode(str(data)))

        steps = self._response_field(response, "steps") or []
        for step in steps:
            if self._response_field(step, "type") != "model_output":
                continue
            for content_block in self._response_field(step, "content") or []:
                if self._response_field(content_block, "type") != "image":
                    continue
                data = self._response_field(content_block, "data")
                if data:
                    return self._decode_image_bytes(base64.b64decode(str(data)))

        raise RuntimeError("Gemini image cleanup returned no decodable image.")

    def _response_error_message(self, response) -> str:
        try:
            data = response.json()
            if isinstance(data, dict):
                err = data.get("error")
                if isinstance(err, dict) and err.get("message"):
                    return str(err["message"])
                if data.get("message"):
                    return str(data["message"])
                if data.get("detail"):
                    return str(data["detail"])
        except Exception:
            pass
        text = getattr(response, "text", "")
        if text:
            return str(text)
        status_code = getattr(response, "status_code", "")
        reason = getattr(response, "reason_phrase", "")
        return f"HTTP {status_code} {reason}".strip()

    @staticmethod
    def _join_url(base_url: str, path: str) -> str:
        base = base_url.rstrip("/")
        endpoint = "/" + path.strip("/")
        if urlparse(base).path.rstrip("/").endswith(endpoint):
            return base
        return f"{base}{endpoint}"

    @staticmethod
    def _is_openrouter_url(base_url: str) -> bool:
        host = urlparse(base_url).netloc.lower()
        return host == "openrouter.ai" or host.endswith(".openrouter.ai")

    @staticmethod
    def _is_gemini_url(base_url: str) -> bool:
        return urlparse(base_url).netloc.lower() == "generativelanguage.googleapis.com"

    @classmethod
    def _gemini_generate_content_url(cls, base_url: str, model: str) -> str:
        base = base_url.rstrip("/")
        parsed = urlparse(base)
        path = parsed.path.rstrip("/")
        if path.endswith(":generateContent"):
            return base
        if path.endswith("/openai"):
            path = path[: -len("/openai")]
            base = urlunparse(
                parsed._replace(path=path, params="", query="", fragment="")
            ).rstrip("/")
        model_path = model if model.startswith("models/") else f"models/{model}"
        return cls._join_url(base, f"/{model_path}:generateContent")

    @staticmethod
    def _png_image_file(img: np.ndarray) -> io.BytesIO:
        if img.ndim != 3 or img.shape[2] < 3:
            raise RuntimeError("LLM image cleanup requires an RGB image.")
        rgb = img[:, :, :3]
        buffer = io.BytesIO()
        Image.fromarray(rgb).save(buffer, format="PNG")
        buffer.seek(0)
        buffer.name = "image.png"
        return buffer

    def _api_args(self, profile: dict, image_file, prompt: str = None) -> Dict:
        return {
            "model": (profile.get("image_model") or "").strip(),
            "image": image_file,
            "prompt": prompt if prompt is not None else self._image_prompt,
        }

    def _openrouter_api_args(self, profile: dict, image_file, prompt: str = None) -> Dict:
        encoded_image = base64.b64encode(image_file.getvalue()).decode("ascii")
        return {
            "model": (profile.get("image_model") or "").strip(),
            "prompt": prompt if prompt is not None else self._image_prompt,
            "input_references": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{encoded_image}",
                    },
                }
            ],
            "output_format": "png",
            "n": 1,
        }

    def _gemini_api_args(self, profile: dict, image_file, prompt: str = None) -> Dict:
        encoded_image = base64.b64encode(image_file.getvalue()).decode("ascii")
        return {
            "contents": [
                {
                    "parts": [
                        {
                            "text": prompt if prompt is not None else self._image_prompt,
                        },
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": encoded_image,
                            },
                        },
                    ],
                },
            ],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
            },
        }

    def _headers(self, api_key: str, json_request: bool = False) -> Dict:
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if json_request:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _gemini_headers(api_key: str) -> Dict:
        return {
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        }

    def _raise_for_response(self, profile: dict, response):
        status_code = getattr(response, "status_code", 200)
        if status_code < 400:
            return
        if status_code in (401, 403):
            raise LLMInpaintError(
                f'API key for profile "{profile.get("name")}" was rejected (HTTP {status_code}).'
            )
        raise RuntimeError(self._response_error_message(response))

    def _request_openrouter_inpaint(self, client, profile: dict, image_file, prompt: str = None) -> np.ndarray:
        base_url = self._image_base_url(profile)
        api_key = self._api_key_for_profile(profile)
        response = client.post(
            self._join_url(base_url, "/images"),
            headers=self._headers(api_key, json_request=True),
            json=self._openrouter_api_args(profile, image_file, prompt=prompt),
        )
        self._raise_for_response(profile, response)
        return self._decode_response_image(response.json())

    def _request_gemini_inpaint(self, client, profile: dict, image_file, prompt: str = None) -> np.ndarray:
        base_url = self._image_base_url(profile)
        api_key = self._api_key_for_profile(profile)
        model = (profile.get("image_model") or "").strip()
        response = client.post(
            self._gemini_generate_content_url(base_url, model),
            headers=self._gemini_headers(api_key),
            json=self._gemini_api_args(profile, image_file, prompt=prompt),
        )
        self._raise_for_response(profile, response)
        return self._decode_gemini_response_image(response.json())

    def _request_openai_compatible_inpaint(self, client, profile: dict, image_file, prompt: str = None) -> np.ndarray:
        base_url = self._image_base_url(profile)
        api_key = self._api_key_for_profile(profile)
        args = self._api_args(profile, image_file, prompt=prompt)
        response = client.post(
            base_url,
            headers=self._headers(api_key),
            data={
                "model": args["model"],
                "prompt": args["prompt"],
            },
            files={
                "image": ("image.png", image_file.getvalue(), "image/png"),
            },
        )
        self._raise_for_response(profile, response)
        return self._decode_response_image(response.json())

    def _request_inpaint(self, profile: dict, img: np.ndarray, prompt: str = None) -> np.ndarray:
        client = self._initialize_client(profile)
        request_img = self._scale_image_for_request(img)
        image_file = self._png_image_file(request_img)
        self._respect_delay()
        try:
            base_url = self._image_base_url(profile)
            if self._is_meshy_url(base_url):
                result = self._request_meshy_inpaint(
                    client, profile, image_file, prompt=prompt, request_img=request_img
                )
            elif self._is_gemini_url(base_url):
                result = self._request_gemini_inpaint(client, profile, image_file, prompt=prompt)
            elif self._is_openrouter_url(base_url):
                result = self._request_openrouter_inpaint(client, profile, image_file, prompt=prompt)
            else:
                result = self._request_openai_compatible_inpaint(client, profile, image_file, prompt=prompt)
            if result.shape[:2] != img.shape[:2]:
                result = cv2.resize(result, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)
            return result
        finally:
            image_file.close()

    # ── Meshy provider (TEMPORARY ─ removable) ─────────────────────
    # Image-to-image via the temporary Meshy API
    # (https://api.meshy.ai/openapi/v1/image-to-image). It is async
    # (create task -> poll -> download) and takes no mask input, so the
    # whole task is re-drawn from the prompt. This region is intentionally
    # self-contained: to drop Meshy support later, delete this whole block
    # (constants + methods) AND remove the ``self._is_meshy_url(...)``
    # branch in ``_request_inpaint``.
    _MESHY_MAX_POLLS = 90
    _MESHY_POLL_INTERVAL = 2.0
    _MESHY_MAX_POLL_ERRORS = 5

    @staticmethod
    def _is_meshy_url(base_url: str) -> bool:
        host = urlparse(base_url).netloc.lower()
        return host == "api.meshy.ai" or host.endswith(".api.meshy.ai")

    @staticmethod
    def _meshy_task_id(payload) -> str:
        if isinstance(payload, dict):
            return str(payload.get("result") or "").strip()
        return str(getattr(payload, "result", "") or "").strip()

    @staticmethod
    def _meshy_image_url(task) -> str:
        if isinstance(task, dict):
            urls = task.get("image_urls") or []
            return str(urls[0]).strip() if urls else ""
        return ""

    @staticmethod
    def _meshy_aspect_ratio(model: str, img: np.ndarray) -> str:
        """Map the input image ratio to the nearest Meshy-supported ratio string.

        Meshy's image-to-image defaults to ``1:1`` and only accepts a fixed set
        of aspect ratios. Forcing a non-square page to ``1:1`` makes the model
        re-compose the whole scene, so the artwork shifts out of alignment when
        only the masked pixels are blended back. Requesting the closest allowed
        ratio keeps the frame close to the original page instead.
        """
        height, width = img.shape[:2]
        ratio = width / height
        if model == "gpt-image-2":
            allowed = (("1:1", 1.0), ("3:2", 1.5), ("2:3", 2.0 / 3.0))
        else:  # nano-banana family and any fallback
            allowed = (
                ("1:1", 1.0),
                ("16:9", 16.0 / 9.0),
                ("9:16", 9.0 / 16.0),
                ("4:3", 4.0 / 3.0),
                ("3:4", 3.0 / 4.0),
            )
        best = min(allowed, key=lambda a: abs(np.log(ratio / a[1])))
        return best[0]

    def _request_meshy_inpaint(
        self, client, profile: dict, image_file, prompt: str = None,
        request_img: np.ndarray = None,
    ) -> np.ndarray:
        base_url = self._image_base_url(profile)
        api_key = self._api_key_for_profile(profile)
        model = (profile.get("image_model") or "").strip()
        encoded_image = base64.b64encode(image_file.getvalue()).decode("ascii")
        create_payload = {
            "ai_model": model,
            "prompt": prompt if prompt is not None else self._image_prompt,
            "reference_image_urls": [f"data:image/png;base64,{encoded_image}"],
        }
        if request_img is not None:
            create_payload["aspect_ratio"] = self._meshy_aspect_ratio(model, request_img)
        create_resp = client.post(
            base_url,
            headers=self._headers(api_key, json_request=True),
            json=create_payload,
        )
        self._raise_for_response(profile, create_resp)
        task_id = self._meshy_task_id(create_resp.json())
        if not task_id:
            raise RuntimeError("Meshy image cleanup returned no task id.")
        poll_url = self._join_url(base_url, f"/{task_id}")
        consecutive_poll_errors = 0
        for _ in range(self._MESHY_MAX_POLLS):
            self._wait(self._MESHY_POLL_INTERVAL)
            try:
                poll_resp = client.get(poll_url, headers=self._headers(api_key))
                self._raise_for_response(profile, poll_resp)
            except LLMInpaintStopped:
                raise
            except Exception as e:
                consecutive_poll_errors += 1
                if consecutive_poll_errors > self._MESHY_MAX_POLL_ERRORS:
                    raise RuntimeError(
                        f"Meshy image cleanup polling failed: {e}"
                    ) from e
                self.logger.warning(
                    f"Meshy poll error "
                    f"({consecutive_poll_errors}/{self._MESHY_MAX_POLL_ERRORS}): {e}"
                )
                continue
            consecutive_poll_errors = 0
            task = poll_resp.json()
            status = str(task.get("status") or "").upper()
            if status == "SUCCEEDED":
                image_url = self._meshy_image_url(task)
                if not image_url:
                    raise RuntimeError("Meshy image cleanup returned no image url.")
                return self._download_image(image_url)
            if status in ("FAILED", "CANCELED"):
                err = task.get("task_error") or {}
                message = (
                    (err.get("message") if isinstance(err, dict) else str(err))
                    or "unknown error"
                )
                raise RuntimeError(f"Meshy image cleanup {status}: {message}")
        raise RuntimeError("Meshy image cleanup timed out.")

    # ── Inpainting ────────────────────────────────────────────────

    def _validate_profile(self, profile: dict):
        if not (profile.get("image_model") or "").strip():
            raise LLMInpaintError(
                f'No image model configured for profile "{profile.get("name")}".'
            )
        self._image_base_url(profile)

    def _is_partial_mask(self, mask: np.ndarray) -> bool:
        covered = float((mask > 127).mean())
        return 0.0 < covered <= _MASK_MIN_SUBSET

    def _highlight_mask(self, img: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Overlay the mask region as a translucent red repair marker so the
        server-side model can spatially see which pixels to erase. ``img`` and
        ``mask`` are always supplied at the same crop-local size, so the marker
        lines up exactly with the region blended back afterwards."""
        m = mask > 127
        if not m.any():
            return img
        tint = np.array([255, 40, 40], dtype=np.float32)  # red marker, RGB (img is RGB)
        out = img.astype(np.float32) * 0.55 + tint * 0.45
        out = np.where(m[..., None], out, img.astype(np.float32))
        return np.clip(out, 0, 255).astype(np.uint8)

    def _inpaint(
        self, img: np.ndarray, mask: np.ndarray, textblock_list: List[TextBlock] = None
    ) -> np.ndarray:
        profile = self._get_active_profile()
        if not profile:
            raise LLMInpaintError("No image-capable LLM profile is configured.")
        self._validate_profile(profile)

        mask_original = (mask > 127)[..., None].astype(np.uint8)
        # When the user annotated a partial region, overlay it with a red repair
        # marker and hand the model a short, focused "erase here" prompt. That
        # keeps its attention on the marked pixels so it reconstructs the
        # background rather than restyling the whole crop. ``img`` and ``mask``
        # are always crop-local and the same size, so the marker lines up exactly
        # with the region that gets blended back afterwards.
        request_img = img
        prompt = None
        if self._is_partial_mask(mask):
            request_img = self._highlight_mask(img, mask)
            prompt = _REGIONAL_PROMPT
        retry_attempt = 0
        max_retries = self.get_param_value("retry attempts")
        while True:
            if self.stop_event is not None and self.stop_event.is_set():
                raise LLMInpaintStopped()
            try:
                result = self._request_inpaint(profile, request_img, prompt=prompt)
                if result.shape[:2] != img.shape[:2]:
                    result = cv2.resize(
                        result, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR
                    )
                result = result.astype(np.uint8, copy=False)
                img_inpainted = result * mask_original + img * (1 - mask_original)
                return img_inpainted
            except (LLMInpaintStopped, LLMInpaintError):
                raise
            except Exception as e:
                retry_attempt += 1
                if retry_attempt >= max_retries:
                    raise RuntimeError(f"LLM image cleanup failed: {e}") from e
                self.logger.warning(
                    f"LLM image cleanup failed due to {e}. Attempt: {retry_attempt}"
                )
                self._wait(self.get_param_value("retry timeout"))
