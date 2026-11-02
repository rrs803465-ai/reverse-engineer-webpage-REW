"""OpenRouter provider adapter.

OpenRouter exposes an OpenAI-compatible chat-completions endpoint at
https://openrouter.ai/api/v1, with its own model-catalogue endpoint at
/models that returns richer metadata including context windows and pricing.
"""

from __future__ import annotations

import logging
from typing import Any

from rea.models import ModelInfo, UsageRecord
from rea.providers.base import ProviderBase, ProviderError, RateLimitError
from rea.providers.openai_compatible import OpenAICompatibleProvider

log = logging.getLogger(__name__)

_OPENROUTER_BASE = "https://openrouter.ai/api/v1"


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"

    def __init__(self, api_key: str, model_id: str, **kwargs: Any) -> None:
        kwargs.setdefault("base_url", _OPENROUTER_BASE)
        kwargs.setdefault("provider_name", "openrouter")
        super().__init__(api_key=api_key, model_id=model_id, **kwargs)

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/rew-project/rew",
            "X-Title": "REW — Reverse Engineer Website",
        }

    async def list_models(self) -> list[ModelInfo]:
        """Fetch OpenRouter's model catalogue with full metadata."""
        url = f"{self.base_url}/models"
        try:
            resp = await self.client.get(url, headers=self._auth_headers())
            self._check_status(resp, self.name)
            data = resp.json()
            models: list[ModelInfo] = []
            for m in data.get("data", []):
                pricing = m.get("pricing", {})
                try:
                    input_price = float(pricing.get("prompt", 0)) * 1_000_000
                    output_price = float(pricing.get("completion", 0)) * 1_000_000
                except (TypeError, ValueError):
                    input_price = output_price = None

                ctx = m.get("context_length")
                supported = m.get("supported_parameters", [])

                models.append(ModelInfo(
                    id=m["id"],
                    name=m.get("name", m["id"]),
                    provider="openrouter",
                    context_window=ctx,
                    input_price_per_mtok=input_price,
                    output_price_per_mtok=output_price,
                    supports_tools="tools" in supported or m.get("top_provider", {}).get("is_moderated", False),
                    supports_vision="image" in m.get("modality", ""),
                    extra={"description": m.get("description", "")},
                ))
            return sorted(models, key=lambda x: x.id)
        except (ProviderError, RateLimitError):
            raise
        except Exception as exc:
            log.warning("OpenRouter model listing failed: %s", exc)
            return []
