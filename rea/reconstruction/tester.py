"""Reconstruction tester: launch the generated project, compare with original."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rea.browser.screenshots import capture, compare_screenshots
from rea.models import PageInfo

log = logging.getLogger(__name__)


@dataclass
class TestResult:
    route: str
    passed: bool = False
    console_errors: list[str] = field(default_factory=list)
    failed_resources: list[str] = field(default_factory=list)
    screenshot_path: str | None = None
    original_screenshot_path: str | None = None
    visual_diff: dict[str, Any] | None = None
    broken_actions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class TestReport:
    project_id: str
    total_routes: int = 0
    passed: int = 0
    failed: int = 0
    results: list[TestResult] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Test Report — Project {self.project_id}",
            f"  Routes tested: {self.total_routes}",
            f"  Passed: {self.passed}  Failed: {self.failed}",
        ]
        for r in self.results:
            status = "✓" if r.passed else "✗"
            diff = ""
            if r.visual_diff:
                diff = f" (visual similarity: {r.visual_diff.get('similarity_pct', '?')}%)"
            lines.append(f"  {status} {r.route}{diff}")
            for note in r.notes:
                lines.append(f"      • {note}")
        if self.suggestions:
            lines.append("\nSuggestions:")
            for s in self.suggestions:
                lines.append(f"  - {s}")
        return "\n".join(lines)


async def test_reconstruction(
    workspace_path: Path,
    original_pages: list[PageInfo],
    project_id: str,
    viewport_width: int = 1280,
    viewport_height: int = 800,
) -> TestReport:
    """Launch the generated project in a local static server and run tests."""
    from playwright.async_api import async_playwright

    report = TestReport(project_id=project_id)
    index_html = workspace_path / "index.html"

    if not index_html.exists():
        report.missing_files.append("index.html")
        report.suggestions.append("Generate index.html first.")
        return report

    # Check which generated files exist.
    generated = {p.name for p in workspace_path.iterdir() if p.is_file()}
    screenshot_dir = workspace_path / "_test_screenshots"
    screenshot_dir.mkdir(exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": viewport_width, "height": viewport_height}
        )

        # Test each HTML file as a route.
        html_files = sorted(workspace_path.glob("*.html"))
        report.total_routes = len(html_files)

        for html_file in html_files:
            route = html_file.name
            file_url = html_file.as_uri()

            result = TestResult(route=route)
            console_errors: list[str] = []
            failed_resources: list[str] = []

            page = await context.new_page()
            page.on("console", lambda m: console_errors.append(
                f"[{m.type}] {m.text[:200]}"
            ) if m.type in ("error", "warn") else None)
            page.on("requestfailed", lambda r: failed_resources.append(r.url))

            try:
                await page.goto(file_url, wait_until="networkidle", timeout=15_000)

                # Screenshot.
                shots = await capture(
                    page,
                    screenshot_dir,
                    name=route,
                    full_page=False,
                )
                if shots:
                    result.screenshot_path = str(shots[0])

                # Visual comparison against original if screenshot exists.
                orig_page = next(
                    (p for p in original_pages if p.screenshot_path), None
                )
                if orig_page and orig_page.screenshot_path and result.screenshot_path:
                    orig_path = Path(orig_page.screenshot_path)
                    if orig_path.exists():
                        result.original_screenshot_path = str(orig_path)
                        result.visual_diff = compare_screenshots(
                            orig_path, Path(result.screenshot_path)
                        )

                # Basic interaction checks.
                result.broken_actions = await _check_basic_interactions(page)

                result.console_errors = [e for e in console_errors if "favicon" not in e.lower()][:10]
                result.failed_resources = [r for r in failed_resources if not _is_expected_miss(r)][:10]

                has_content = await page.evaluate(
                    "() => document.body.innerText.trim().length > 0"
                )

                result.passed = (
                    has_content
                    and len([e for e in result.console_errors if "[error]" in e]) == 0
                )

                if result.visual_diff:
                    sim = result.visual_diff.get("similarity_pct", 0)
                    if isinstance(sim, (int, float)) and sim < 60:
                        result.notes.append(
                            f"Low visual similarity ({sim}%) — consider regenerating."
                        )
                        result.passed = False

            except Exception as exc:
                result.notes.append(f"Test error: {exc}")
                result.passed = False
            finally:
                await page.close()

            if result.passed:
                report.passed += 1
            else:
                report.failed += 1
            report.results.append(result)

        await browser.close()

    # Suggestions.
    if report.failed > 0:
        report.suggestions.append(
            "Run: python3 ccopecode.py scan <url> --improve to let the AI fix gaps."
        )
    css_files = list(workspace_path.glob("*.css"))
    if not css_files:
        report.suggestions.append("No CSS file found — styles may be missing.")

    return report


async def _check_basic_interactions(page: Any) -> list[str]:
    """Check for obviously broken interactive elements."""
    broken = []
    try:
        # Check for broken images.
        broken_imgs = await page.evaluate("""
            () => Array.from(document.images)
                .filter(img => !img.complete || img.naturalWidth === 0)
                .map(img => img.src).slice(0, 5)
        """)
        for img in broken_imgs:
            broken.append(f"Broken image: {img[:100]}")
    except Exception:
        pass
    return broken


def _is_expected_miss(url: str) -> bool:
    """Ignore known-missing resources that don't indicate a real problem."""
    expected = {".ico", "favicon"}
    return any(e in url.lower() for e in expected)
