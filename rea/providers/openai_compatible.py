"""OpenAI and OpenAI-compatible provider adapter."""

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
    AuthError, ProviderBase, ProviderError, RateLimitError,
)

log = logging.getLogger(__name__)

_OPENAI_BASE = "https://api.openai.com/v1"


class OpenAICompatibleProvider(ProviderBase):
    name = "openai_compatible"

    def __init__(
        self,
        api_key: str,
        model_id: str,
        base_url: str | None = None,
        provider_name: str = "openai_compatible",
        **kwargs: Any,
    ) -> None:
        super().__init__(api_key=api_key, model_id=model_id, **kwargs)
        self.base_url = (base_url or _OPENAI_BASE).rstrip("/")
        self.name = provider_name

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Model listing
    # ------------------------------------------------------------------

    async def list_models(self) -> list[ModelInfo]:
        url = f"{self.base_url}/models"
        try:
            resp = await self.client.get(url, headers=self._auth_headers())
            self._check_status(resp, self.name)
            data = resp.json()
            models = []
            for m in data.get("data", []):
                mid = m.get("id", "")
                models.append(ModelInfo(
                    id=mid,
                    name=mid,
                    provider=self.name,
                    extra=m,
                ))
            return sorted(models, key=lambda x: x.id)
        except (ProviderError, AuthError, RateLimitError):
            raise
        except Exception as exc:
            log.warning("Model listing failed for %s: %s", self.name, exc)
            return []

    async def validate_connection(self) -> bool:
        try:
            # Minimal completion to verify auth.
            text, _ = await self.generate_response(
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            return True
        except AuthError:
            return False
        except Exception as exc:
            log.debug("Connection validation error: %s", exc)
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
                f"{self.base_url}/chat/completions",
                headers=self._auth_headers(),
                json=payload,
            )
            self._check_status(resp, self.name)
            data = resp.json()
            text = data["choices"][0]["message"]["content"] or ""
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
        payload = self._build_payload(messages, system, max_tokens, temperature=0.0)
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

        async def _call() -> tuple[str | None, list[dict[str, Any]], UsageRecord]:
            resp = await self.client.post(
                f"{self.base_url}/chat/completions",
                headers=self._auth_headers(),
                json=payload,
            )
            self._check_status(resp, self.name)
            data = resp.json()
            choice = data["choices"][0]["message"]
            text = choice.get("content")
            raw_calls = choice.get("tool_calls") or []
            tool_calls = []
            for tc in raw_calls:
                args = tc["function"].get("arguments", "{}")
                try:
                    parsed_args = json.loads(args)
                except json.JSONDecodeError:
                    parsed_args = {"_raw": args}
                tool_calls.append({
                    "id": tc.get("id", uuid.uuid4().hex),
                    "name": tc["function"]["name"],
                    "arguments": parsed_args,
                })
            return text, tool_calls, self._parse_usage(data)

        return await self._with_retry(_call)

    def estimate_tokens(self, text: str) -> int:
        try:
            import tiktoken
            enc = tiktoken.encoding_for_model("gpt-4")
            return len(enc.encode(text))
        except Exception:
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
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)
        return {
            "model": self.model_id,
            "messages": full_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

    def _parse_usage(self, data: dict[str, Any]) -> UsageRecord:
        u = data.get("usage", {})
        return UsageRecord(
            provider=self.name,
            model_id=self.model_id,
            input_tokens=u.get("prompt_tokens", 0),
            output_tokens=u.get("completion_tokens", 0),
            cached_tokens=u.get("prompt_tokens_details", {}).get("cached_tokens", 0),
            source="api",
        )
