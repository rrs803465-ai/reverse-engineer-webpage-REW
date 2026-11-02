"""Playwright network event collection with secret redaction."""

from __future__ import annotations

import logging
import time
from typing import Any

from playwright.async_api import Page, Request, Response

from rea.models import NetworkEvent
from rea.security.redaction import redact_headers, redact_url, safe_truncate

log = logging.getLogger(__name__)

_SKIP_TYPES = {"image", "font", "media", "websocket"}  # skip large binary assets by default
_MAX_BODY_CHARS = 400


class NetworkCollector:
    """Attach to a Playwright page and collect sanitized network events."""

    def __init__(self, page: Page, capture_assets: bool = False) -> None:
        self._page = page
        self._capture_assets = capture_assets
        self._events: list[NetworkEvent] = []
        self._pending: dict[str, float] = {}  # url -> start time

        page.on("request", self._on_request)
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_failed)

    def _on_request(self, request: Request) -> None:
        rtype = request.resource_type
        if not self._capture_assets and rtype in _SKIP_TYPES:
            return
        self._pending[request.url] = time.monotonic()

    def _on_response(self, response: Response) -> None:
        url = response.url
        start = self._pending.pop(url, None)
        duration = (time.monotonic() - start) * 1000 if start else None

        rtype = response.request.resource_type
        if not self._capture_assets and rtype in _SKIP_TYPES:
            return

        is_xhr = rtype in {"xhr", "fetch"}
        safe_url = redact_url(url)

        event = NetworkEvent(
            method=response.request.method,
            url=safe_url,
            resource_type=rtype,
            status=response.status,
            content_type=response.headers.get("content-type", ""),
            duration_ms=duration,
            is_xhr=is_xhr,
        )
        self._events.append(event)

    def _on_failed(self, request: Request) -> None:
        self._pending.pop(request.url, None)
        safe_url = redact_url(request.url)
        event = NetworkEvent(
            method=request.method,
            url=safe_url,
            resource_type=request.resource_type,
            status=None,
            request_body_summary="[request failed]",
        )
        self._events.append(event)

    def drain(self) -> list[NetworkEvent]:
        """Return collected events and reset the buffer."""
        events = list(self._events)
        self._events.clear()
        return events

    @property
    def events(self) -> list[NetworkEvent]:
        return list(self._events)

    def api_events(self) -> list[NetworkEvent]:
        """Return only XHR/fetch events (likely API calls)."""
        return [e for e in self._events if e.is_xhr]
