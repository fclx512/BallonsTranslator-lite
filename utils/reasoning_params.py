"""
Normalize reasoning_effort across LLM providers.

Semantic scale:
    "" (默认，不覆写) | none | minimal | low | medium | high | xhigh | max

Maps each semantic level to the provider's native API parameter format at call time.
Adding a new provider: add a key to _PROVIDER_MAPPERS.
"""

from __future__ import annotations

from typing import Dict


# ── Provider detection ──────────────────────────────────────────────


def detect_provider(api_host: str, model: str = "") -> str:
    """Identify the LLM provider from the API endpoint URL."""
    host = api_host.lower().strip()
    if not host:
        return "unknown"
    if "openai.com" in host:
        return "openai"
    if "anthropic.com" in host:
        return "anthropic"
    if "deepseek.com" in host:
        return "deepseek"
    if "openrouter.ai" in host:
        return "openrouter"
    if "googleapis.com" in host:
        return "gemini"
    if "localhost" in host or "127.0.0.1" in host:
        return "local"
    return "unknown"


# ── Per-provider mapper helpers ─────────────────────────────────────


def _openai(effort: str) -> Dict[str, str]:
    """OpenAI: top-level reasoning_effort param (o-series, GPT-5)."""
    # OpenAI accepts: none / low / medium / high
    M = {"minimal": "low", "xhigh": "high", "max": "high"}
    return {"reasoning_effort": M.get(effort, effort)}


def _anthropic(effort: str) -> Dict[str, object]:
    """Claude: thinking.adaptive + output_config.effort (4.6+)."""
    return {
        "extra_body": {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
        }
    }


def _deepseek(effort: str) -> Dict[str, str]:
    """DeepSeek: reasoning_effort — only high/max meaningful; others → high."""
    return {"reasoning_effort": "max" if effort in ("xhigh", "max") else "high"}


def _openrouter(effort: str) -> Dict[str, object]:
    """OpenRouter: normalized reasoning.effort."""
    return {"reasoning": {"effort": effort}}


def _gemini(effort: str) -> Dict[str, object]:
    """Gemini: thinkingConfig.thinkingLevel."""
    M = {"low": "LOW", "medium": "MEDIUM",
         "high": "HIGH", "xhigh": "HIGH", "max": "HIGH"}
    return {"thinkingConfig": {"thinkingLevel": M.get(effort, "MEDIUM")}}


# ── Registry ────────────────────────────────────────────────────────

# Each mapper returns a dict of kwargs to **merge into the request.
# Args: effort (str) — already known to be non-empty and != "none".
# Return {} to skip.

_PROVIDER_MAPPERS = {
    "openai": _openai,
    "anthropic": _anthropic,
    "deepseek": _deepseek,
    "openrouter": _openrouter,
    "gemini": _gemini,
    "local": lambda _: {},  # local models rarely support reasoning
}


# ── Public API ──────────────────────────────────────────────────────


def build_reasoning_kwargs(
    api_host: str, effort: str = "", model: str = ""
) -> Dict[str, object]:
    """Convert semantic reasoning_effort → provider-native API kwargs.

    Args:
        api_host: Endpoint URL (used to detect the provider).
        effort: Semantic level — ``""`` / ``"none"`` → no override.
        model:  Model name (reserved for future disambiguation).

    Returns:
        Dict of kwargs to **-merge into the ``client.chat.completions.create()``
        call, or ``{}`` when no override is wanted.
    """
    effort = (effort or "").strip().lower()
    if not effort or effort in ("", "none", "default"):
        return {}

    provider = detect_provider(api_host, model)
    mapper = _PROVIDER_MAPPERS.get(provider)
    if mapper:
        return mapper(effort)

    # Unknown provider: pass as a top-level param — many OpenAI-compatible
    # endpoints understand this convention.
    return {"reasoning_effort": effort}
