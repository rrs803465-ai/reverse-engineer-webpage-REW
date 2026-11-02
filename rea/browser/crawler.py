"""Multi-page crawler using BFS with configurable depth and page limits."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page

from rea.browser.actions import discover_actions
from rea.browser.inspector import inspect_page
from rea.browser.network import NetworkCollector
from rea.browser.screenshots import capture
from rea.models import DiscoveredAction, NetworkEvent, PageInfo
from rea.security.url_validation import is_same_origin, normalize_url, validate_url

log = logging.getLogger(__name__)

PageCallback = Callable[
    [PageInfo, list[DiscoveredAction], list[NetworkEvent]],
    Coroutine[Any, Any, None],
]


@dataclass
class CrawlResult:
    pages: list[PageInfo] = field(default_factory=list)
    actions: list[DiscoveredAction] = field(default_factory=list)
    network_events: list[NetworkEvent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def crawl(
    context: BrowserContext,
    start_url: str,
    max_pages: int = 20,
    max_depth: int = 3,
    screenshot_dir: Any = None,
    full_page_screenshots: bool = False,
    already_visited: set[str] | None = None,
    on_page: PageCallback | None = None,
    discover_page_actions: bool = True,
) -> CrawlResult:
    """BFS crawl from *start_url* collecting page info, actions, and network events.

    *already_visited* lets a resumed scan skip previously crawled URLs.
    *on_page* is an async callback called after each page is fully processed.
    """
    result = CrawlResult()
    visited: set[str] = already_visited or set()

    norm_start = normalize_url(start_url)
    queue: deque[tuple[str, int]] = deque([(norm_start, 0)])

    while queue and len(result.pages) < max_pages:
        raw_url, depth = queue.popleft()
        url = normalize_url(raw_url)

        if url in visited:
            continue

        # Validate before visiting.
        try:
            validate_url(url)
        except Exception as exc:
            log.debug("Skipping invalid URL %s: %s", url, exc)
            continue

        if not is_same_origin(start_url, url):
            continue

        visited.add(url)

        page = await context.new_page()
        net = NetworkCollector(page)
        console_errors: list[str] = []
        failed_reqs: list[str] = []

        page.on("console", lambda msg: console_errors.append(
            f"[{msg.type}] {msg.text[:200]}"
        ) if msg.type in ("error", "warn") else None)
        page.on("requestfailed", lambda req: failed_reqs.append(req.url))

        try:
            log.info("Crawling [depth=%d]: %s", depth, url)
            resp = await page.goto(url, wait_until="networkidle", timeout=30_000)

            page_info = await inspect_page(page, start_url)
            page_info.status_code = resp.status if resp else None
            page_info.console_errors = console_errors[:20]
            page_info.failed_requests = [r for r in failed_reqs[:20] if not _is_asset(r)]

            # Screenshots.
            if screenshot_dir:
                from pathlib import Path
                shot_dir = Path(screenshot_dir)
                shots = await capture(
                    page,
                    shot_dir,
                    name=url,
                    full_page=full_page_screenshots,
                    widths=[375, 768, 1280] if full_page_screenshots else None,
                )
                if shots:
                    page_info.screenshot_path = str(shots[0])

            # Action discovery.
            actions: list[DiscoveredAction] = []
            if discover_page_actions:
                try:
                    actions = await discover_actions(page, url)
                except Exception as exc:
                    log.debug("Action discovery failed on %s: %s", url, exc)

            # Network events.
            events = net.drain()

            result.pages.append(page_info)
            result.actions.extend(actions)
            result.network_events.extend(events)

            if on_page:
                await on_page(page_info, actions, events)

            # Enqueue discovered links.
            if depth < max_depth:
                for link in page_info.links:
                    norm_link = normalize_url(link)
                    if norm_link not in visited and is_same_origin(start_url, norm_link):
                        queue.append((norm_link, depth + 1))

        except Exception as exc:
            log.warning("Failed to crawl %s: %s", url, exc)
            result.errors.append(f"{url}: {exc}")
        finally:
            try:
                await page.close()
            except Exception:
                pass

        # Small polite delay between page loads.
        await asyncio.sleep(0.3)

    return result


def _is_asset(url: str) -> bool:
    ext = urlparse(url).path.rsplit(".", 1)[-1].lower()
    return ext in {"png", "jpg", "jpeg", "gif", "webp", "svg", "ico",
                   "woff", "woff2", "ttf", "otf", "mp4", "webm", "mp3"}
