"""Asset indexer: download, classify, hash, and cache public website assets."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

from rea.analysis.css_analyzer import analyze_css
from rea.analysis.js_analyzer import analyze_js
from rea.models import AssetInfo
from rea.security.redaction import redact_url

log = logging.getLogger(__name__)

_ASSET_TYPES: dict[str, str] = {
    ".css": "css",
    ".js": "js",
    ".mjs": "js",
    ".jsx": "js",
    ".ts": "js",
    ".tsx": "js",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".svg": "svg",
    ".ico": "image",
    ".woff": "font",
    ".woff2": "font",
    ".ttf": "font",
    ".otf": "font",
    ".json": "manifest",
    ".webmanifest": "manifest",
    ".xml": "manifest",
    ".txt": "manifest",
}

_TEXT_TYPES = {"css", "js", "svg", "manifest"}
_MAX_TEXT_BYTES = 5 * 1024 * 1024   # 5 MB cap per asset
_CONCURRENCY = 4


async def index_assets(
    urls: list[str],
    cache_dir: Path,
    existing_hashes: dict[str, str] | None = None,
) -> tuple[list[AssetInfo], dict[str, Any]]:
    """Download and analyze a list of asset URLs.

    *existing_hashes* maps url → content_hash for previously downloaded assets
    so unchanged files are not re-fetched.

    Returns (asset_list, analysis_by_url).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    existing_hashes = existing_hashes or {}

    sem = asyncio.Semaphore(_CONCURRENCY)

    if _HAS_HTTPX:
        import httpx
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            tasks = [
                _fetch_asset_httpx(client, url, cache_dir, existing_hashes, sem)
                for url in urls
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
    else:
        # Fallback: run requests in a thread pool.
        tasks = [
            _fetch_asset_requests(url, cache_dir, existing_hashes, sem)
            for url in urls
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    assets: list[AssetInfo] = []
    analysis: dict[str, Any] = {}

    for url, result in zip(urls, results):
        if isinstance(result, Exception):
            log.debug("Asset fetch failed for %s: %s", redact_url(url), result)
            continue
        asset, detail = result
        assets.append(asset)
        if detail:
            analysis[url] = detail

    return assets, analysis


async def _fetch_asset_httpx(
    client: Any,
    url: str,
    cache_dir: Path,
    existing_hashes: dict[str, str],
    sem: asyncio.Semaphore,
) -> tuple[AssetInfo, dict[str, Any] | None]:
    async with sem:
        return await asyncio.get_event_loop().run_in_executor(
            None, _fetch_sync, url, cache_dir, existing_hashes
        )


async def _fetch_asset_requests(
    url: str,
    cache_dir: Path,
    existing_hashes: dict[str, str],
    sem: asyncio.Semaphore,
) -> tuple[AssetInfo, dict[str, Any] | None]:
    async with sem:
        return await asyncio.get_event_loop().run_in_executor(
            None, _fetch_sync, url, cache_dir, existing_hashes
        )


def _fetch_sync(
    url: str,
    cache_dir: Path,
    existing_hashes: dict[str, str],
) -> tuple[AssetInfo, dict[str, Any] | None]:
    """Synchronous fetch using requests (or httpx if available)."""
    asset_type = _classify_url(url)
    safe_name = _url_to_filename(url)
    local_path = cache_dir / safe_name

    # Cache hit.
    if local_path.exists() and url in existing_hashes:
        content = local_path.read_bytes()
        if _hash(content) == existing_hashes[url]:
            log.debug("Asset cache hit: %s", safe_name)
            return _build_asset(url, asset_type, content, str(local_path)), None

    try:
        import requests as req_lib
        resp = req_lib.get(url, timeout=20, allow_redirects=True)
        resp.raise_for_status()
        content = resp.content
    except Exception as exc:
        raise RuntimeError(f"HTTP error fetching {redact_url(url)}: {exc}") from exc

    if len(content) > _MAX_TEXT_BYTES:
        content = content[:_MAX_TEXT_BYTES]

    local_path.write_bytes(content)
    asset = _build_asset(url, asset_type, content, str(local_path))
    detail = _analyze_content(content, asset_type, url)

    if detail:
        asset.extracted_vars = detail.get("css_variables", {})
        asset.fonts = detail.get("font_families", [])
        asset.colors = detail.get("colors", [])

    return asset, detail


def _build_asset(url: str, asset_type: str, content: bytes, local_path: str) -> AssetInfo:
    return AssetInfo(
        url=url,
        asset_type=asset_type,
        file_size_bytes=len(content),
        content_hash=_hash(content),
        local_path=local_path,
    )


def _analyze_content(content: bytes, asset_type: str, url: str) -> dict[str, Any] | None:
    if asset_type not in _TEXT_TYPES:
        return None
    try:
        text = content.decode("utf-8", errors="replace")
    except Exception:
        return None

    if asset_type == "css":
        return analyze_css(text, url)
    if asset_type == "js":
        return analyze_js(text, url)
    return None


def _classify_url(url: str) -> str:
    path = urlparse(url).path.lower()
    ext = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    return _ASSET_TYPES.get(ext, "other")


def _url_to_filename(url: str) -> str:
    h = hashlib.md5(url.encode()).hexdigest()[:8]
    parsed = urlparse(url)
    name = parsed.path.rsplit("/", 1)[-1] or "index"
    safe = "".join(c if c.isalnum() or c in ".-_" else "_" for c in name)
    return f"{h}_{safe[:40]}"


def _hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:20]


def merge_design_tokens(analyses: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge CSS analyses from multiple stylesheets into one design-token summary."""
    all_vars: dict[str, str] = {}
    all_colors: list[str] = []
    all_fonts: list[str] = []
    all_breakpoints: list[int] = []

    for a in analyses:
        all_vars.update(a.get("css_variables", {}))
        all_colors.extend(a.get("colors", []))
        all_fonts.extend(a.get("font_families", []))
        all_breakpoints.extend(a.get("breakpoints_px", []))

    return {
        "css_variables": all_vars,
        "colors": list(dict.fromkeys(all_colors))[:40],
        "fonts": list(dict.fromkeys(all_fonts))[:10],
        "breakpoints": sorted(set(all_breakpoints)),
    }
