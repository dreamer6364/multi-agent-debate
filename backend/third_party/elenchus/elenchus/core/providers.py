"""LLM provider abstraction — keeps the bot model/reasoning-level agnostic.

Every provider exposes the same call:

    analyze(system, user, schema, effort) -> dict

returning a dict that validates against `schema`. SDK imports are lazy so the
core package imports cleanly without every provider's SDK installed.

Providers raise typed errors (ProviderRefusal / ProviderRateLimited /
MalformedOutput / ProviderError) so the adapters can show friendly messages
instead of leaking raw SDK exceptions.
"""
from __future__ import annotations

import json
import re

from .config import Config


class ProviderError(Exception):
    """Generic provider failure."""


class ProviderRefusal(ProviderError):
    """The model declined to answer for safety reasons."""


class ProviderRateLimited(ProviderError):
    """The provider is rate-limited or out of quota (HTTP 429 / insufficient_quota)."""


class MalformedOutput(ProviderError):
    """The model returned something that isn't valid, well-formed JSON."""


def _is_rate_limit(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) == 429:
        return True
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return "ratelimit" in name or "insufficient_quota" in text or "too many requests" in text


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):  # tolerate ```json fences
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise MalformedOutput("Model did not return valid JSON.") from exc


class AnthropicProvider:
    def __init__(self, model: str, api_key: str | None = None):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        self.model = model

    def analyze(self, system: str, user: str, schema: dict, effort: str) -> dict:
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=system,
                thinking={"type": "adaptive"},
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": user}],
            )
        except ProviderError:
            raise
        except Exception as exc:
            if _is_rate_limit(exc):
                raise ProviderRateLimited(str(exc)) from exc
            raise ProviderError(str(exc)) from exc
        if resp.stop_reason == "refusal":
            raise ProviderRefusal("The model declined to analyze this message.")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if not text:
            raise MalformedOutput("Model returned no text content.")
        return _extract_json(text)


class OpenAIProvider:
    """Works with OpenAI and any OpenAI-compatible API (OpenRouter, Ollama, ...)."""

    _EFFORTS = {"low", "medium", "high"}

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def analyze(self, system: str, user: str, schema: dict, effort: str) -> dict:
        params: dict = dict(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "elenchus_findings", "schema": schema, "strict": True},
            },
        )
        if effort in self._EFFORTS:
            params["reasoning_effort"] = effort
        resp = self._create(params)
        return _extract_json(resp.choices[0].message.content or "")

    @staticmethod
    def _nudge_json(params: dict) -> dict:
        params = dict(params)
        params["messages"] = list(params["messages"]) + [
            {"role": "user", "content": "Respond ONLY with a single valid JSON object with the requested fields."}
        ]
        return params

    def _create(self, params: dict):
        # Try the ideal request, then progressively degrade for OpenAI-compatible
        # backends (Google, Ollama, ...) that reject reasoning_effort or strict schema.
        variants = [params]
        if "reasoning_effort" in params:
            variants.append({k: v for k, v in params.items() if k != "reasoning_effort"})
        base = variants[-1]
        rf = base.get("response_format")
        if isinstance(rf, dict) and rf.get("type") == "json_schema":
            variants.append(self._nudge_json({**base, "response_format": {"type": "json_object"}}))
            variants.append(self._nudge_json({k: v for k, v in base.items() if k != "response_format"}))

        last_exc: Exception | None = None
        for variant in variants:
            try:
                return self.client.chat.completions.create(**variant)
            except Exception as exc:
                last_exc = exc
                if _is_rate_limit(exc):
                    break  # degrading won't fix a rate limit / quota error
        assert last_exc is not None
        if _is_rate_limit(last_exc):
            raise ProviderRateLimited(str(last_exc)) from last_exc
        raise ProviderError(str(last_exc)) from last_exc


class StubProvider:
    """Offline provider — returns canned findings without any API call or key.

    Selected with LLM_PROVIDER=stub. Useful for testing rendering, rate limiting,
    and error paths. Magic substrings in the message trigger specific outcomes:
      __refuse__     -> ProviderRefusal
      __ratelimit__  -> ProviderRateLimited
      __error__      -> ProviderError
      __empty__      -> zero findings
    """

    def __init__(self, model: str | None = None):
        self.model = model or "stub"

    def analyze(self, system: str, user: str, schema: dict, effort: str) -> dict:
        low = user.lower()
        if "__refuse__" in low:
            raise ProviderRefusal("Stubbed refusal.")
        if "__ratelimit__" in low:
            raise ProviderRateLimited("Stubbed rate limit.")
        if "__error__" in low:
            raise ProviderError("Stubbed error.")

        match = re.search(r"<message>\n(.*)\n</message>", user, re.S)
        body = (match.group(1) if match else user).strip()
        if "__empty__" in low or len(body) < 40:
            return {"overall": "[stub] Nothing notable found.", "findings": []}
        return {
            "overall": "[stub] Sample analysis (no API used).",
            "findings": [
                {
                    "name": "[stub] Example finding",
                    "quote": body[:60],
                    "explanation": "This is a stubbed result for offline testing.",
                    "confidence": "medium",
                }
            ],
        }


# Google exposes an OpenAI-compatible endpoint, so a Google key runs through the
# same OpenAI client path — no separate SDK dependency.
GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def build_provider(cfg: Config):
    if cfg.provider == "stub":
        return StubProvider(cfg.model)
    if cfg.provider == "anthropic":
        return AnthropicProvider(cfg.model, cfg.anthropic_api_key)
    if cfg.provider in ("openai", "openai-compatible"):
        return OpenAIProvider(cfg.model, cfg.openai_api_key, cfg.openai_base_url)
    if cfg.provider in ("google", "gemini"):
        return OpenAIProvider(cfg.model, cfg.google_api_key, GOOGLE_BASE_URL)
    raise ValueError(
        f"Unknown LLM_PROVIDER: {cfg.provider!r} (use 'anthropic', 'openai', 'google', or 'stub')"
    )
