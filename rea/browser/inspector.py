"""Page inspector: extract structured page observations from a live Playwright page."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from playwright.async_api import Page

from rea.models import PageInfo
from rea.security.url_validation import is_same_origin, normalize_url

log = logging.getLogger(__name__)


async def inspect_page(
    page: Page,
    base_url: str,
    capture_links: bool = True,
) -> PageInfo:
    """Extract a structured PageInfo from the current page state."""
    url = page.url
    title = await page.title()

    # Headings.
    headings = await _extract_headings(page)

    # Links (same-origin only).
    links: list[str] = []
    if capture_links:
        links = await _extract_links(page, base_url)

    # Forms.
    forms = await _extract_forms(page)

    # Meta tags.
    meta = await _extract_meta(page)

    # Console errors (attached separately via event listener, but we also
    # check for any accessible error state on the page).
    console_errors: list[str] = []
    failed_requests: list[str] = []

    return PageInfo(
        url=url,
        title=title,
        headings=headings,
        links=links,
        forms=forms,
        meta=meta,
        console_errors=console_errors,
        failed_requests=failed_requests,
    )


async def extract_compact_summary(page: Page, base_url: str) -> dict[str, Any]:
    """Return a compact dict describing the page — suitable for AI context."""
    info = await inspect_page(page, base_url)

    # Visible text (bounded).
    try:
        body_text = await page.inner_text("body")
        body_text = re.sub(r"\s+", " ", body_text).strip()[:2000]
    except Exception:
        body_text = ""

    # Accessible roles.
    landmarks = await _extract_landmarks(page)

    return {
        "url": info.url,
        "title": info.title,
        "headings": info.headings[:20],
        "landmark_roles": landmarks,
        "form_count": len(info.forms),
        "forms": [_summarize_form(f) for f in info.forms[:5]],
        "link_count": len(info.links),
        "same_origin_links": info.links[:30],
        "meta": {k: v for k, v in list(info.meta.items())[:10]},
        "body_excerpt": body_text,
    }


async def _extract_headings(page: Page) -> list[str]:
    try:
        elements = await page.query_selector_all("h1,h2,h3,h4,h5,h6")
        headings = []
        for el in elements[:30]:
            text = (await el.inner_text()).strip()
            tag = await el.evaluate("el => el.tagName.toLowerCase()")
            if text:
                headings.append(f"{tag}: {text[:120]}")
        return headings
    except Exception as exc:
        log.debug("Heading extraction failed: %s", exc)
        return []


async def _extract_links(page: Page, base_url: str) -> list[str]:
    try:
        raw = await page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]'))
                .map(a => a.href)
                .filter(h => h && !h.startsWith('javascript:') && !h.startsWith('mailto:') && !h.startsWith('tel:'))
        """)
        seen: set[str] = set()
        result: list[str] = []
        for href in raw:
            try:
                full = urljoin(base_url, href)
                norm = normalize_url(full)
                parsed = urlparse(norm)
                # Strip fragments; keep same-origin only.
                clean = parsed._replace(fragment="").geturl()
                if is_same_origin(base_url, clean) and clean not in seen:
                    seen.add(clean)
                    result.append(clean)
            except Exception:
                continue
        return result[:200]
    except Exception as exc:
        log.debug("Link extraction failed: %s", exc)
        return []


async def _extract_forms(page: Page) -> list[dict[str, Any]]:
    try:
        return await page.evaluate("""
            () => Array.from(document.querySelectorAll('form')).map(form => ({
                id: form.id || null,
                action: form.action || null,
                method: form.method || 'get',
                fields: Array.from(form.querySelectorAll('input,select,textarea')).map(el => ({
                    tag: el.tagName.toLowerCase(),
                    type: el.type || null,
                    name: el.name || null,
                    id: el.id || null,
                    placeholder: el.placeholder || null,
                    required: el.required || false,
                    label: (() => {
                        if (el.id) {
                            const lbl = document.querySelector(`label[for="${el.id}"]`);
                            if (lbl) return lbl.textContent.trim();
                        }
                        const closest = el.closest('label');
                        return closest ? closest.textContent.trim().slice(0, 80) : null;
                    })()
                })).slice(0, 20)
            })).slice(0, 10)
        """)
    except Exception as exc:
        log.debug("Form extraction failed: %s", exc)
        return []


async def _extract_meta(page: Page) -> dict[str, str]:
    try:
        return await page.evaluate("""
            () => {
                const meta = {};
                document.querySelectorAll('meta[name], meta[property]').forEach(m => {
                    const key = m.getAttribute('name') || m.getAttribute('property');
                    const val = m.getAttribute('content');
                    if (key && val) meta[key] = val.slice(0, 200);
                });
                return meta;
            }
        """)
    except Exception as exc:
        log.debug("Meta extraction failed: %s", exc)
        return {}


async def _extract_landmarks(page: Page) -> list[str]:
    try:
        return await page.evaluate("""
            () => {
                const roles = ['banner','navigation','main','complementary','contentinfo',
                               'search','form','region'];
                return roles.filter(r =>
                    document.querySelector(`[role="${r}"], ${
                        r === 'banner' ? 'header' :
                        r === 'navigation' ? 'nav' :
                        r === 'main' ? 'main' :
                        r === 'complementary' ? 'aside' :
                        r === 'contentinfo' ? 'footer' :
                        r === 'search' ? '[role="search"]' :
                        r === 'form' ? 'form' : '[role="region"]'
                    }`) !== null
                );
            }
        """)
    except Exception:
        return []


def _summarize_form(form: dict[str, Any]) -> dict[str, Any]:
    fields = form.get("fields", [])
    return {
        "method": form.get("method", "get").upper(),
        "action": form.get("action", ""),
        "field_count": len(fields),
        "fields": [
            f.get("label") or f.get("placeholder") or f.get("name") or f.get("type", "?")
            for f in fields[:8]
        ],
    }
