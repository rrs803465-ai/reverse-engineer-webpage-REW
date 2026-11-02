"""Evidence builder: assembles compact, AI-ready context from scan data.

The contract here: AI never sees raw HTML, full CSS bundles, or minified JS.
It sees structured summaries with bounded length.
"""

from __future__ import annotations

import json
from typing import Any

from rea.models import (
    AssetInfo, DiscoveredAction, NetworkEvent, PageInfo, Project,
)


def build_project_summary(
    project: Project,
    pages: list[PageInfo],
    actions: list[DiscoveredAction],
    network_events: list[NetworkEvent],
    assets: list[AssetInfo],
    design_tokens: dict[str, Any] | None = None,
    js_analysis: list[dict[str, Any]] | None = None,
) -> str:
    """Return a compact Markdown summary for AI context injection."""
    lines: list[str] = []

    # Header.
    lines.append(f"# REW Project: {project.name}")
    lines.append(f"**Target:** {project.target_url}")
    lines.append(f"**Status:** {project.status.value}")
    lines.append("")

    # Pages.
    lines.append(f"## Pages Scanned ({len(pages)})")
    for p in pages[:30]:
        status = f" [{p.status_code}]" if p.status_code else ""
        lines.append(f"- `{p.url}`{status} — {p.title or '(no title)'}")
        if p.headings:
            lines.append(f"  Headings: {', '.join(p.headings[:4])}")
        if p.console_errors:
            lines.append(f"  ⚠ Console errors: {len(p.console_errors)}")
    lines.append("")

    # Actions summary.
    if actions:
        lines.append(f"## Discovered Actions ({len(actions)})")
        by_page: dict[str, list[DiscoveredAction]] = {}
        for a in actions:
            by_page.setdefault(a.page_url, []).append(a)

        for url, page_actions in list(by_page.items())[:10]:
            lines.append(f"\n**{url}**")
            for a in page_actions[:12]:
                confidence = f"[{a.confidence.value}]"
                result = f" → {a.observed_result}" if a.observed_result else ""
                lines.append(f"  {a.id} | {a.element_type} | {a.label or '?'} {confidence}{result}")
        lines.append("")

    # Network events — API calls only.
    api_events = [e for e in network_events if e.is_xhr]
    if api_events:
        lines.append(f"## Observed API Calls ({len(api_events)})")
        seen_endpoints: set[str] = set()
        for ev in api_events[:30]:
            key = f"{ev.method} {ev.url}"
            if key not in seen_endpoints:
                seen_endpoints.add(key)
                status = f" → {ev.status}" if ev.status else ""
                lines.append(f"- `{ev.method} {ev.url}`{status} ({ev.content_type or 'unknown type'})")
        lines.append("")

    # Assets.
    if assets:
        by_type: dict[str, list[AssetInfo]] = {}
        for a in assets:
            by_type.setdefault(a.asset_type, []).append(a)
        lines.append(f"## Assets ({len(assets)} total)")
        for atype, alist in sorted(by_type.items()):
            lines.append(f"  {atype}: {len(alist)} files")
        lines.append("")

    # Design tokens.
    if design_tokens:
        lines.append("## Design Tokens (from CSS)")
        colors = design_tokens.get("colors", [])
        fonts = design_tokens.get("fonts", [])
        bps = design_tokens.get("breakpoints", [])
        vars_ = design_tokens.get("css_variables", {})

        if fonts:
            lines.append(f"  Fonts: {', '.join(fonts[:6])}")
        if colors:
            lines.append(f"  Colors: {', '.join(colors[:12])}")
        if bps:
            lines.append(f"  Breakpoints: {', '.join(str(b) + 'px' for b in bps)}")
        if vars_:
            lines.append(f"  CSS variables: {len(vars_)} defined")
            for k, v in list(vars_.items())[:8]:
                lines.append(f"    {k}: {v}")
        lines.append("")

    # JS / framework signals.
    if js_analysis:
        frameworks: list[str] = []
        all_routes: list[str] = []
        for ja in js_analysis:
            frameworks.extend(ja.get("frameworks", []))
            all_routes.extend(ja.get("routes", []))
        if frameworks:
            lines.append(f"## Detected Frameworks: {', '.join(set(frameworks))}")
        if all_routes:
            lines.append(f"## Client-Side Routes ({len(all_routes)})")
            for r in sorted(set(all_routes))[:20]:
                lines.append(f"  {r}")
        lines.append("")

    return "\n".join(lines)


def build_page_evidence(
    page: PageInfo,
    actions: list[DiscoveredAction],
    network_events: list[NetworkEvent],
    max_chars: int = 3000,
) -> str:
    """Compact evidence for a single page — used in targeted AI tasks."""
    lines: list[str] = [
        f"# Page: {page.url}",
        f"Title: {page.title or '(none)'}",
        f"Status: {page.status_code or 'unknown'}",
    ]

    if page.headings:
        lines.append(f"\nHeadings: {', '.join(page.headings[:8])}")

    if page.forms:
        lines.append(f"\nForms ({len(page.forms)}):")
        for f in page.forms[:3]:
            method = f.get("method", "GET")
            action = f.get("action", "")
            fields = [fi.get("label") or fi.get("name") or fi.get("type", "?")
                      for fi in f.get("fields", [])[:6]]
            lines.append(f"  [{method}] {action} — fields: {', '.join(fields)}")

    page_actions = [a for a in actions if a.page_url == page.url]
    if page_actions:
        lines.append(f"\nActions ({len(page_actions)}):")
        for a in page_actions[:10]:
            r = f" → {a.observed_result}" if a.observed_result else ""
            lines.append(f"  {a.id} | {a.element_type} | {a.label or '?'} [{a.confidence.value}]{r}")

    api_calls = [e for e in network_events if e.is_xhr]
    if api_calls:
        lines.append(f"\nAPI calls ({len(api_calls)}):")
        for ev in api_calls[:8]:
            lines.append(f"  {ev.method} {ev.url} → {ev.status}")

    if page.console_errors:
        lines.append(f"\nConsole errors ({len(page.console_errors)}):")
        for e in page.console_errors[:4]:
            lines.append(f"  {e[:120]}")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... [truncated]"
    return text


def build_reconstruction_brief(
    project: Project,
    pages: list[PageInfo],
    actions: list[DiscoveredAction],
    design_tokens: dict[str, Any],
    generated_files: list[dict],
    target_route: str | None = None,
) -> str:
    """Brief for the AI reconstruction task — what to build and with what evidence."""
    lines: list[str] = [
        "# Reconstruction Task",
        f"Target URL: {project.target_url}",
        f"Requested route: {target_route or '(all discovered routes)'}",
        "",
        "## What has been observed:",
    ]

    for p in pages[:10]:
        if target_route and target_route not in p.url:
            continue
        lines.append(f"- `{p.url}`: {p.title or '?'} "
                     f"({len([a for a in actions if a.page_url == p.url])} actions, "
                     f"{len(p.forms)} forms)")

    lines.append("\n## Design tokens available:")
    if design_tokens.get("fonts"):
        lines.append(f"  Fonts: {', '.join(design_tokens['fonts'][:4])}")
    if design_tokens.get("colors"):
        lines.append(f"  Primary colors: {', '.join(design_tokens['colors'][:8])}")
    if design_tokens.get("breakpoints"):
        lines.append(f"  Responsive breakpoints: {design_tokens['breakpoints']}")

    if generated_files:
        lines.append(f"\n## Already generated ({len(generated_files)} files):")
        for f in generated_files[:10]:
            lines.append(f"  {f['relative_path']} — {f.get('purpose', '?')}")

    lines.append(
        "\n## Constraints:\n"
        "- Use only observed behavior and design tokens — do not invent unobserved functionality.\n"
        "- Mark any mock data clearly with a comment.\n"
        "- No backend secrets, credentials, or private user data.\n"
        "- Keep generated files modular and maintainable.\n"
        "- Use the simplest frontend stack that reproduces the observed behavior."
    )

    return "\n".join(lines)
