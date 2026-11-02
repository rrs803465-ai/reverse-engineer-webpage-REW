"""CSS analysis: extract design tokens, colors, fonts, breakpoints — all local."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

# Patterns
_VAR_DEF = re.compile(r"(--[\w-]+)\s*:\s*([^;}{]+)")
_COLOR_HEX = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6,8})\b")
_COLOR_RGB = re.compile(r"rgba?\(\s*[\d.,\s%]+\)")
_COLOR_HSL = re.compile(r"hsla?\(\s*[\d.,\s%]+\)")
_FONT_FAMILY = re.compile(r"font-family\s*:\s*([^;}{]+)")
_FONT_FACE_FAMILY = re.compile(r"@font-face\s*\{[^}]*font-family\s*:\s*['\"]?([^;'\"}{]+)['\"]?", re.S)
_BREAKPOINT = re.compile(r"@media[^{]*\((?:min|max)-width\s*:\s*([\d.]+)(px|em|rem)\s*\)")
_KEYFRAME = re.compile(r"@keyframes\s+([\w-]+)")
_CLASS_PATTERN = re.compile(r"\.([\w-]+)\s*\{")
_GRID = re.compile(r"display\s*:\s*grid", re.I)
_FLEX = re.compile(r"display\s*:\s*flex", re.I)


def analyze_css(css: str, url: str = "") -> dict[str, Any]:
    """Return a structured design-token summary from raw CSS text."""
    content_hash = hashlib.sha256(css.encode()).hexdigest()[:16]

    css_vars = _extract_vars(css)
    colors = _extract_colors(css, css_vars)
    fonts = _extract_fonts(css)
    breakpoints = _extract_breakpoints(css)
    animations = _KEYFRAME.findall(css)
    uses_grid = bool(_GRID.search(css))
    uses_flex = bool(_FLEX.search(css))
    class_names = list(set(_CLASS_PATTERN.findall(css)))[:50]

    return {
        "url": url,
        "content_hash": content_hash,
        "size_bytes": len(css.encode()),
        "css_variables": css_vars,
        "colors": colors,
        "font_families": fonts,
        "breakpoints_px": breakpoints,
        "animations": animations[:20],
        "uses_grid": uses_grid,
        "uses_flex": uses_flex,
        "class_sample": class_names[:40],
        "line_count": css.count("\n"),
    }


def _extract_vars(css: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in _VAR_DEF.findall(css):
        result[name.strip()] = value.strip()[:100]
    return dict(list(result.items())[:100])


def _extract_colors(css: str, css_vars: dict[str, str]) -> list[str]:
    seen: set[str] = set()
    colors: list[str] = []

    # Colors from variable values.
    for val in css_vars.values():
        _add_colors(val, seen, colors)

    # Hex colors.
    for match in _COLOR_HEX.finditer(css):
        c = match.group().lower()
        if c not in seen:
            seen.add(c)
            colors.append(c)

    # RGB/RGBA.
    for match in _COLOR_RGB.finditer(css):
        c = match.group()
        if c not in seen:
            seen.add(c)
            colors.append(c)

    # HSL/HSLA.
    for match in _COLOR_HSL.finditer(css):
        c = match.group()
        if c not in seen:
            seen.add(c)
            colors.append(c)

    return colors[:60]


def _add_colors(text: str, seen: set[str], result: list[str]) -> None:
    for pattern in (_COLOR_HEX, _COLOR_RGB, _COLOR_HSL):
        for m in pattern.finditer(text):
            c = m.group()
            if c not in seen:
                seen.add(c)
                result.append(c)


def _extract_fonts(css: str) -> list[str]:
    seen: set[str] = set()
    fonts: list[str] = []

    # @font-face declarations.
    for match in _FONT_FACE_FAMILY.finditer(css):
        f = match.group(1).strip().strip("'\"")
        if f and f not in seen:
            seen.add(f)
            fonts.append(f)

    # font-family properties.
    for match in _FONT_FAMILY.finditer(css):
        raw = match.group(1).strip()
        for part in raw.split(","):
            f = part.strip().strip("'\"")
            # Skip generic families.
            if f.lower() not in {"serif", "sans-serif", "monospace", "cursive", "fantasy", "inherit", "initial", "var"}:
                if f and f not in seen:
                    seen.add(f)
                    fonts.append(f)

    return fonts[:20]


def _extract_breakpoints(css: str) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for val, unit in _BREAKPOINT.findall(css):
        try:
            px = float(val)
            if unit == "em":
                px = px * 16
            elif unit == "rem":
                px = px * 16
            ipx = int(px)
            if ipx not in seen:
                seen.add(ipx)
                result.append(ipx)
        except ValueError:
            continue
    return sorted(result)
