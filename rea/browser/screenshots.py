"""Screenshot capture and comparison utilities."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from playwright.async_api import Page

log = logging.getLogger(__name__)


async def capture(
    page: Page,
    output_dir: Path,
    name: str,
    full_page: bool = False,
    widths: list[int] | None = None,
) -> list[Path]:
    """Capture one or more screenshots of *page*.

    If *widths* is provided, resize the viewport for each width and capture
    a responsive screenshot per width.  Always restores the original viewport.

    Returns a list of saved screenshot paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    original_size = page.viewport_size or {"width": 1280, "height": 800}

    target_widths = widths or [original_size["width"]]

    for width in target_widths:
        height = max(600, int(width * 0.75))
        await page.set_viewport_size({"width": width, "height": height})

        suffix = f"_{width}w" if len(target_widths) > 1 else ""
        filename = output_dir / f"{_safe_name(name)}{suffix}.png"

        try:
            await page.screenshot(
                path=str(filename),
                full_page=full_page,
                type="png",
            )
            saved.append(filename)
            log.debug("Screenshot saved: %s", filename)
        except Exception as exc:
            log.warning("Screenshot failed for %s@%dpx: %s", name, width, exc)

    # Restore original viewport.
    await page.set_viewport_size(
        {"width": original_size["width"], "height": original_size["height"]}
    )
    return saved


def compare_screenshots(path_a: Path, path_b: Path) -> dict[str, Any]:
    """Compute a pixel-difference score between two PNG screenshots.

    Returns a dict with keys: pixels_different, total_pixels, diff_ratio,
    and a human-readable summary.  Uses Pillow if available; falls back to
    a hash-based comparison.
    """
    try:
        from PIL import Image, ImageChops
        import math

        img_a = Image.open(path_a).convert("RGB")
        img_b = Image.open(path_b).convert("RGB")

        # Resize to smaller of the two for apples-to-apples comparison.
        w = min(img_a.width, img_b.width)
        h = min(img_a.height, img_b.height)
        img_a = img_a.resize((w, h))
        img_b = img_b.resize((w, h))

        diff = ImageChops.difference(img_a, img_b)
        hist = diff.histogram()

        # Sum of squared differences across all channels.
        sq_diff = sum(
            (val / 255.0) ** 2 * count
            for val, count in enumerate(hist)
        )
        total = w * h * 3  # RGB channels
        rms = math.sqrt(sq_diff / total) if total > 0 else 0.0

        # Count non-black pixels in diff (any channel > threshold).
        pixels_different = sum(
            1 for r, g, b in diff.getdata()
            if r > 8 or g > 8 or b > 8
        )
        total_pixels = w * h
        diff_ratio = pixels_different / total_pixels if total_pixels > 0 else 0.0

        return {
            "pixels_different": pixels_different,
            "total_pixels": total_pixels,
            "diff_ratio": round(diff_ratio, 4),
            "rms_error": round(rms, 4),
            "similarity_pct": round((1 - diff_ratio) * 100, 1),
            "method": "pixel_diff",
            "summary": f"{diff_ratio * 100:.1f}% of pixels differ (RMS: {rms:.3f})",
        }

    except ImportError:
        # Pillow not installed — fall back to SHA256 hash comparison.
        hash_a = hashlib.sha256(path_a.read_bytes()).hexdigest()
        hash_b = hashlib.sha256(path_b.read_bytes()).hexdigest()
        identical = hash_a == hash_b
        return {
            "pixels_different": None,
            "total_pixels": None,
            "diff_ratio": 0.0 if identical else 1.0,
            "similarity_pct": 100.0 if identical else 0.0,
            "method": "hash",
            "summary": "Identical (hash match)" if identical else "Different (hash mismatch)",
        }


def _safe_name(name: str) -> str:
    """Convert a URL or label into a filesystem-safe filename stem."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    return safe[:80] or "page"
