"""Abstract provider base class with retry, rate-limit, and token accounting."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from rea.models import ModelInfo, UsageRecord

log = logging.getLogger(__name__)


class ProviderError(Exception):
    """Generic provider error."""


class AuthError(ProviderError):
    """Authentication failure — do not retry."""


class RateLimitError(ProviderError):
    """HTTP 429. Carries retry_after seconds if the header was present."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ContextLengthError(ProviderError):
    """Request exceeds model context window."""


class ModelCapabilityError(ProviderError):
    """Model does not support the requested feature (tools, vision, etc.)."""


class ProviderBase(ABC):
    """Common interface all provider adapters must implement."""

    name: str = "base"

    def __init__(
        self,
        api_key: str,
        model_id: str,
        base_url: str | None = None,
        timeout_seconds: int = 60,
        max_retries: int = 5,
    ) -> None:
        self.api_key = api_key
        self.model_id = model_id
        self.base_url = base_url
        self.timeout = timeout_seconds
        self.max_retries = max_retries

        self._client: httpx.AsyncClient | None = None
        self._usage: list[UsageRecord] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "ProviderBase":
        if httpx is None:
            raise RuntimeError(
                "httpx is required for AI provider calls. Install it: pip install httpx"
            )
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> Any:
        if self._client is None:
            raise RuntimeError("Provider not entered as async context manager.")
        return self._client

    # ------------------------------------------------------------------
    # Required interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """Return available models for this provider."""

    @abstractmethod
    async def validate_connection(self) -> bool:
        """Return True if credentials and connectivity are valid."""

    @abstractmethod
    async def generate_response(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> tuple[str, UsageRecord]:
        """Non-streaming completion. Returns (text, usage)."""

    @abstractmethod
    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> tuple[str | None, list[dict[str, Any]], UsageRecord]:
        """Tool-calling completion.

        Returns (text_response, tool_calls, usage).
        tool_calls is a list of {"name": str, "arguments": dict, "id": str}.
        text_response may be None if the model only emitted tool calls.
        """

    @abstractmethod
    def estimate_tokens(self, text: str) -> int:
        """Return a token estimate. May use a local tokenizer or character heuristic."""

    # ------------------------------------------------------------------
    # Retry / backoff helper
    # ------------------------------------------------------------------

    async def _with_retry(self, coro_fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Call coro_fn with exponential backoff on transient errors."""
        attempt = 0
        while True:
            try:
                return await coro_fn(*args, **kwargs)
            except RateLimitError as exc:
                attempt += 1
                if attempt > self.max_retries:
                    raise
                wait = exc.retry_after or self._backoff(attempt)
                log.warning(
                    "Rate limited by %s. Waiting %.1fs (attempt %d/%d).",
                    self.name, wait, attempt, self.max_retries,
                )
                await asyncio.sleep(wait)
            except Exception as exc:
                # Catch httpx network errors by name to avoid import-time dependency.
                exc_type = type(exc).__name__
                if exc_type not in ("ConnectTimeout", "ReadTimeout", "RemoteProtocolError",
                                    "ConnectError", "NetworkError"):
                    raise
                attempt += 1
                if attempt > self.max_retries:
                    raise ProviderError(f"Network error after {attempt} attempts: {exc}") from exc
                wait = self._backoff(attempt)
                log.warning("Transient network error: %s. Retrying in %.1fs.", exc, wait)
                await asyncio.sleep(wait)
            except AuthError:
                raise  # Never retry auth failures.

    @staticmethod
    def _backoff(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
        """Exponential backoff with full jitter."""
        delay = min(cap, base * (2 ** attempt))
        return random.uniform(0, delay)

    @staticmethod
    def _parse_retry_after(headers: Any) -> float | None:
        val = headers.get("retry-after")
        if val is None:
            return None
        try:
            return float(val)
        except ValueError:
            return None

    @staticmethod
    def _check_status(response: Any, provider_name: str) -> None:
        if response.status_code == 401:
            raise AuthError(f"{provider_name}: invalid API key (HTTP 401).")
        if response.status_code == 429:
            retry_after = ProviderBase._parse_retry_after(response.headers)
            raise RateLimitError(
                f"{provider_name}: rate limited (HTTP 429).",
                retry_after=retry_after,
            )
        if response.status_code == 400:
            body = response.text[:300]
            if "context" in body.lower() or "token" in body.lower():
                raise ContextLengthError(f"{provider_name}: context length exceeded.")
            raise ProviderError(f"{provider_name}: bad request: {body}")
        if response.status_code >= 500:
            raise ProviderError(
                f"{provider_name}: server error {response.status_code}: {response.text[:200]}"
            )
        try:
            response.raise_for_status()
        except Exception as exc:
            raise ProviderError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Shared token estimation fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _char_token_estimate(text: str) -> int:
        """Rough character-based token estimate (≈4 chars per token)."""
        return max(1, len(text) // 4)
