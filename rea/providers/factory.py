"""Provider factory: build a ProviderBase from a stored ProviderConfig + API key."""

from __future__ import annotations

from typing import Any

from rea.config import get_api_key
from rea.models import ProviderConfig, ProviderType
from rea.providers.base import ProviderBase, ProviderError
from rea.providers.openai_compatible import OpenAICompatibleProvider
from rea.providers.openrouter import OpenRouterProvider
from rea.providers.anthropic import AnthropicProvider


def build_provider(name: str, cfg: ProviderConfig) -> ProviderBase:
    """Instantiate and return the appropriate ProviderBase subclass.

    The API key is retrieved from secure storage here, not from the config.
    Raises ProviderError if the key is missing or the type is unknown.
    """
    api_key = get_api_key(name)
    if not api_key:
        raise ProviderError(
            f"No API key found for provider '{name}'. "
            f"Set environment variable REW_{name.upper()}_API_KEY or run: "
            f"python3 ccopecode.py config"
        )

    model_id = cfg.model_id or ""
    common: dict[str, Any] = {
        "timeout_seconds": cfg.timeout_seconds,
        "max_retries": cfg.max_retries,
    }

    if cfg.type == ProviderType.OPENROUTER:
        return OpenRouterProvider(api_key=api_key, model_id=model_id, **common)

    if cfg.type == ProviderType.ANTHROPIC:
        return AnthropicProvider(api_key=api_key, model_id=model_id, **common)

    if cfg.type in (ProviderType.OPENAI, ProviderType.COMPATIBLE):
        return OpenAICompatibleProvider(
            api_key=api_key,
            model_id=model_id,
            base_url=cfg.base_url,
            provider_name=name,
            **common,
        )

    raise ProviderError(f"Unknown provider type: {cfg.type}")
