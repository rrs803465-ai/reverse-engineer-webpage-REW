"""Anthropic native API adapter.

Uses the /v1/messages endpoint directly. Tool calling uses Anthropic's
native tool-use format, not the OpenAI schema.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

try:
    import httpx  # noqa: F401
except ImportError:
    pass  # handled in base

from rea.models import ModelInfo, UsageRecord
from rea.providers.base import (
    AuthError,
    ContextLengthError,
    ProviderBase,
    ProviderError,
    RateLimitError,
)

log = logging.getLogger(__name__)

_ANTHROPIC_BASE = "https://api.anthropic.com"
_API_VERSION = "2023-06-01"

# Models known at implementation time; the /v1/models endpoint is used
# when available, falling back to this list.
_KNOWN_MODELS: list[dict[str, Any]] = [
    {"id": "claude-opus-4-5",    "name": "Claude Opus 4.5",    "context_window": 200_000},
    {"id": "claude-sonnet-4-5",  "name": "Claude Sonnet 4.5",  "context_window": 200_000},
    {"id": "claude-haiku-4-5",   "name": "Claude Haiku 4.5",   "context_window": 200_000},
    {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet", "context_window": 200_000},
    {"id": "claude-3-haiku-20240307",    "name": "Claude 3 Haiku",    "context_window": 200_000},
]


class AnthropicProvider(ProviderBase):
    name = "anthropic"

    def __init__(self, api_key: str, model_id: str, **kwargs: Any) -> None:
        kwargs.pop("base_url", None)  # Anthropic base URL is fixed.
        super().__init__(api_key=api_key, model_id=model_id, **kwargs)
        self.base_url = _ANTHROPIC_BASE

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
        }

    # ------------------------------------------------------------------
    # Model listing
    # ------------------------------------------------------------------

    async def list_models(self) -> list[ModelInfo]:
        try:
            resp = await self.client.get(
                f"{self.base_url}/v1/models",
                headers=self._headers(),
            )
            if resp.status_code == 200:
                data = resp.json()
                models = []
                for m in data.get("data", []):
                    models.append(ModelInfo(
                        id=m["id"],
                        name=m.get("display_name", m["id"]),
                        provider="anthropic",
                        supports_tools=True,
                        supports_vision=True,
                        context_window=m.get("context_window"),
                        extra=m,
                    ))
                return sorted(models, key=lambda x: x.id)
        except Exception as exc:
            log.debug("Anthropic /v1/models failed (%s); using known list.", exc)

        # Fallback: return statically known models.
        return [
            ModelInfo(
                id=m["id"],
                name=m["name"],
                provider="anthropic",
                context_window=m["context_window"],
                supports_tools=True,
                supports_vision=True,
            )
            for m in _KNOWN_MODELS
        ]

    async def validate_connection(self) -> bool:
        try:
            _, _ = await self.generate_response(
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return True
        except AuthError:
            return False
        except Exception as exc:
            log.debug("Anthropic connection validation: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Completions
    # ------------------------------------------------------------------

    async def generate_response(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> tuple[str, UsageRecord]:
        payload = self._build_payload(messages, system, max_tokens, temperature)

        async def _call() -> tuple[str, UsageRecord]:
            resp = await self.client.post(
                f"{self.base_url}/v1/messages",
                headers=self._headers(),
                json=payload,
            )
            self._check_status(resp, self.name)
            data = resp.json()
            text = self._extract_text(data)
            usage = self._parse_usage(data)
            return text, usage

        return await self._with_retry(_call)

    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> tuple[str | None, list[dict[str, Any]], UsageRecord]:
        # Convert from OpenAI tool schema to Anthropic tool schema.
        anthropic_tools = []
        for t in tools:
            fn = t.get("function", t)
            anthropic_tools.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })

        payload = self._build_payload(messages, system, max_tokens, temperature=0.0)
        payload["tools"] = anthropic_tools

        async def _call() -> tuple[str | None, list[dict[str, Any]], UsageRecord]:
            resp = await self.client.post(
                f"{self.base_url}/v1/messages",
                headers=self._headers(),
                json=payload,
            )
            self._check_status(resp, self.name)
            data = resp.json()

            text_parts = []
            tool_calls = []

            for block in data.get("content", []):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": block.get("id", uuid.uuid4().hex),
                        "name": block["name"],
                        "arguments": block.get("input", {}),
                    })

            text = "\n".join(text_parts) if text_parts else None
            return text, tool_calls, self._parse_usage(data)

        return await self._with_retry(_call)

    def estimate_tokens(self, text: str) -> int:
        # Anthropic uses a similar tokenization to cl100k; character heuristic
        # is close enough for budget estimation.
        return self._char_token_estimate(text)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        system: str | None,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system
        return payload

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        parts = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)

    def _parse_usage(self, data: dict[str, Any]) -> UsageRecord:
        u = data.get("usage", {})
        return UsageRecord(
            provider=self.name,
            model_id=self.model_id,
            input_tokens=u.get("input_tokens", 0),
            output_tokens=u.get("output_tokens", 0),
            cached_tokens=u.get("cache_read_input_tokens", 0),
            source="api",
        )

    def _check_status(self, response: Any, provider_name: str) -> None:  # type: ignore[override]
        """Anthropic uses 'error' key in the body instead of HTTP status for some errors."""
        if hasattr(response, "status_code"):
            if response.status_code == 401:
                raise AuthError("Anthropic: invalid API key (HTTP 401).")
            if response.status_code == 429:
                retry_after = self._parse_retry_after(response.headers)
                raise RateLimitError("Anthropic: rate limited.", retry_after=retry_after)
            if response.status_code == 400:
                try:
                    body = response.json()
                    msg = body.get("error", {}).get("message", response.text[:200])
                except Exception:
                    msg = response.text[:200]
                if "context" in msg.lower() or "token" in msg.lower():
                    raise ContextLengthError(f"Anthropic: {msg}")
                raise ProviderError(f"Anthropic: {msg}")
            if response.status_code >= 500:
                raise ProviderError(f"Anthropic server error {response.status_code}")
            try:
                response.raise_for_status()
            except Exception as exc:
                raise ProviderError(str(exc)) from exc
