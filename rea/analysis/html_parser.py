"""Local HTML analysis using BeautifulSoup — no AI calls needed here."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)


def parse_html(html: str, base_url: str = "") -> dict[str, Any]:
    """Parse raw HTML and return a structured, compact summary.

    Everything here is deterministic — no API calls.
    """
    soup = BeautifulSoup(html, "html.parser")

    return {
        "title": _title(soup),
        "meta": _meta(soup),
        "headings": _headings(soup),
        "links": _links(soup, base_url),
        "images": _images(soup, base_url),
        "scripts": _scripts(soup, base_url),
        "stylesheets": _stylesheets(soup, base_url),
        "forms": _forms(soup),
        "semantic_structure": _semantic_structure(soup),
        "content_hash": hashlib.sha256(html.encode()).hexdigest()[:16],
        "char_count": len(html),
    }


def _title(soup: BeautifulSoup) -> str | None:
    tag = soup.find("title")
    return tag.get_text(strip=True) if tag else None


def _meta(soup: BeautifulSoup) -> dict[str, str]:
    result: dict[str, str] = {}
    for m in soup.find_all("meta"):
        key = m.get("name") or m.get("property")
        val = m.get("content")
        if key and val:
            result[str(key)] = str(val)[:200]
    return dict(list(result.items())[:20])


def _headings(soup: BeautifulSoup) -> list[str]:
    result = []
    for tag in soup.find_all(re.compile(r"^h[1-6]$")):
        text = tag.get_text(strip=True)
        if text:
            result.append(f"{tag.name}: {text[:120]}")
    return result[:30]


def _links(soup: BeautifulSoup, base_url: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        full = urljoin(base_url, href) if base_url else href
        # Strip fragment.
        parsed = urlparse(full)
        clean = parsed._replace(fragment="").geturl()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result[:150]


def _images(soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
    result = []
    for img in soup.find_all("img")[:40]:
        src = img.get("src") or img.get("data-src") or ""
        if src and base_url:
            src = urljoin(base_url, src)
        result.append({
            "src": src[:300],
            "alt": img.get("alt", "")[:100],
            "width": img.get("width"),
            "height": img.get("height"),
        })
    return result


def _scripts(soup: BeautifulSoup, base_url: str) -> list[str]:
    srcs = []
    for s in soup.find_all("script", src=True):
        src = str(s["src"])
        if base_url:
            src = urljoin(base_url, src)
        srcs.append(src[:300])
    return srcs[:30]


def _stylesheets(soup: BeautifulSoup, base_url: str) -> list[str]:
    hrefs = []
    for link in soup.find_all("link", rel=True):
        rels = link.get("rel", [])
        if isinstance(rels, list):
            rels = [r.lower() for r in rels]
        else:
            rels = [str(rels).lower()]
        if "stylesheet" in rels:
            href = link.get("href", "")
            if href:
                if base_url:
                    href = urljoin(base_url, href)
                hrefs.append(str(href)[:300])
    return hrefs[:20]


def _forms(soup: BeautifulSoup) -> list[dict[str, Any]]:
    result = []
    for form in soup.find_all("form")[:10]:
        fields = []
        for el in form.find_all(["input", "select", "textarea"])[:20]:
            label_text = _find_label(soup, el)
            fields.append({
                "tag": el.name,
                "type": el.get("type", "text") if el.name == "input" else el.name,
                "name": el.get("name"),
                "placeholder": el.get("placeholder"),
                "label": label_text,
                "required": el.has_attr("required"),
            })
        result.append({
            "action": form.get("action", ""),
            "method": form.get("method", "get").upper(),
            "fields": fields,
        })
    return result


def _find_label(soup: BeautifulSoup, el: Tag) -> str | None:
    el_id = el.get("id")
    if el_id:
        lbl = soup.find("label", attrs={"for": el_id})
        if lbl:
            return lbl.get_text(strip=True)[:80]
    parent = el.parent
    while parent and parent.name != "form":
        if parent.name == "label":
            return parent.get_text(strip=True)[:80]
        parent = parent.parent
    return None


def _semantic_structure(soup: BeautifulSoup) -> list[str]:
    """Return a flat list of semantic landmark tags present."""
    landmarks = ["header", "nav", "main", "aside", "footer", "section", "article", "search"]
    found = []
    for tag in landmarks:
        if soup.find(tag):
            found.append(tag)
    # Role-based.
    for role in ["banner", "navigation", "main", "complementary", "contentinfo", "search"]:
        if soup.find(attrs={"role": role}):
            label = f'[role="{role}"]'
            if label not in found:
                found.append(label)
    return found
