"""Playwright browser lifecycle manager."""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import (
    Browser, BrowserContext, Page,
    async_playwright, Playwright,
)

from rea.security.url_validation import URLValidationError, validate_url

log = logging.getLogger(__name__)


class BrowserManager:
    """Manages a single Playwright browser instance and its contexts.

    Usage::

        async with BrowserManager(headless=True) as bm:
            async with bm.new_context() as ctx:
                page = await ctx.new_page()
                ...
    """

    def __init__(
        self,
        browser_type: str = "chromium",
        headless: bool = True,
        viewport_width: int = 1280,
        viewport_height: int = 800,
        request_timeout_ms: int = 30_000,
        allowed_domains: list[str] | None = None,
    ) -> None:
        self.browser_type = browser_type
        self.headless = headless
        self.viewport = {"width": viewport_width, "height": viewport_height}
        self.request_timeout_ms = request_timeout_ms
        self.allowed_domains = allowed_domains or []

        self._pw: Playwright | None = None
        self._browser: Browser | None = None

    async def __aenter__(self) -> "BrowserManager":
        self._pw = await async_playwright().start()
        launcher = getattr(self._pw, self.browser_type)
        self._browser = await launcher.launch(
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        log.debug("Browser launched: %s (headless=%s)", self.browser_type, self.headless)
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._browser = None
        self._pw = None

    @property
    def browser(self) -> Browser:
        if self._browser is None:
            raise RuntimeError("BrowserManager not started.")
        return self._browser

    async def new_context(
        self,
        extra_headers: dict[str, str] | None = None,
    ) -> BrowserContext:
        ctx = await self.browser.new_context(
            viewport=self.viewport,
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            extra_http_headers=extra_headers or {},
            ignore_https_errors=False,
        )
        ctx.set_default_timeout(self.request_timeout_ms)
        ctx.set_default_navigation_timeout(self.request_timeout_ms)

        # Block navigation to private/disallowed networks at the browser level.
        await ctx.route("**/*", self._route_handler)
        return ctx

    async def _route_handler(self, route: Any, request: Any) -> None:
        """Intercept requests and block disallowed destinations."""
        url = request.url
        try:
            validate_url(
                url,
                allowed_domains=self.allowed_domains if self.allowed_domains else None,
                resolve_dns=False,  # DNS resolved at Python level before navigation.
            )
            await route.continue_()
        except URLValidationError as exc:
            log.warning("Blocking browser request to: %s — %s", url, exc)
            await route.abort("blockedbyclient")

    async def new_page(self, context: BrowserContext | None = None) -> Page:
        ctx = context or await self.new_context()
        page = await ctx.new_page()
        return page
