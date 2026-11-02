"""Reconstruction generator: produces frontend files from scan evidence via AI."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from rea.agent.context import ContextManager
from rea.agent.token_budget import BudgetExceededError, TokenBudget
from rea.models import (
    AssetInfo, DiscoveredAction, NetworkEvent, PageInfo, Project,
)
from rea.providers.base import ProviderBase
from rea.reconstruction.workspace import Workspace
from rea.storage.projects import ProjectStore
from rea.storage.usage import UsageStore

log = logging.getLogger(__name__)

_FILE_BLOCK = re.compile(
    r"```(?:html|css|js|javascript|json|tsx?|jsx?|svelte|vue)?\s*\n"
    r"(?:/{2,3}\s*(?P<filename>[\w./\-]+\.\w+)\s*\n)?"
    r"(?P<code>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)

_FILENAME_COMMENT = re.compile(
    r"(?:^|\n)\s*(?://|/\*|#|<!--)\s*(?:file|filename|path):\s*([\w./\-]+\.\w+)",
    re.IGNORECASE,
)


class ReconstructionGenerator:
    """Generates frontend source files from scan evidence."""

    def __init__(
        self,
        provider: ProviderBase,
        project: Project,
        workspace: Workspace,
        pages: list[PageInfo],
        actions: list[DiscoveredAction],
        network_events: list[NetworkEvent],
        assets: list[AssetInfo],
        design_tokens: dict[str, Any],
        js_analysis: list[dict[str, Any]],
        budget: TokenBudget,
        project_store: ProjectStore,
        usage_store: UsageStore,
    ) -> None:
        self.provider = provider
        self.project = project
        self.workspace = workspace
        self.pages = pages
        self.actions = actions
        self.network_events = network_events
        self.assets = assets
        self.design_tokens = design_tokens
        self.js_analysis = js_analysis
        self.budget = budget
        self.project_store = project_store
        self.usage_store = usage_store

        self._context = ContextManager(
            project, pages, actions, network_events, assets,
            design_tokens, js_analysis,
        )

    async def generate(
        self,
        target_route: str | None = None,
        improve_existing: bool = False,
    ) -> list[str]:
        """Generate or improve frontend files for *target_route* (or all routes).

        Returns a list of relative paths of files written.
        """
        generated_files = self.project_store.get_generated_files(self.project.id)

        task = self._build_generation_task(target_route, improve_existing, generated_files)
        messages = self._context.messages_for_reconstruction(
            task=task,
            generated_files=generated_files,
            target_route=target_route,
        )

        est_input = self._context.estimate_message_tokens(messages, self.provider.estimate_tokens)
        self.budget.check_input(est_input)
        self.budget.check_output(4096)
        self.budget.check_requests()

        log.info("Requesting reconstruction from AI (%d est. input tokens).", est_input)

        text, usage = await self.provider.generate_response(
            messages=messages,
            system=ContextManager.SYSTEM_PROMPT,
            max_tokens=4096,
            temperature=0.1,
        )

        usage.project_id = self.project.id
        usage.task_label = "reconstruction"
        self.usage_store.record(usage)
        self.budget.record_request(
            usage.input_tokens, usage.output_tokens, usage.estimated_cost_usd
        )

        written = self._extract_and_write_files(text)

        if not written:
            # No code blocks found — write raw response as a markdown report.
            fallback = "reconstruction_notes.md"
            self.workspace.write(fallback, text)
            written = [fallback]
            log.warning("No code blocks found in AI response — saved as notes.")

        return written

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_generation_task(
        self,
        target_route: str | None,
        improve_existing: bool,
        generated_files: list[dict],
    ) -> str:
        if improve_existing and generated_files:
            return (
                "Review the already-generated files listed above. "
                "Identify the most significant visual or behavioral gap versus the original. "
                "Output improved versions of the files that address that gap. "
                "Include the filename as a comment on the first line of each code block."
            )

        if target_route:
            page = next((p for p in self.pages if target_route in p.url), None)
            page_desc = f"\nPage title: {page.title}" if page else ""
            return (
                f"Generate a complete, working HTML/CSS/JS reconstruction of: {target_route}{page_desc}\n"
                "Use only observed evidence. Include filename comments in each code block.\n"
                "Create separate files for HTML, CSS, and any JavaScript.\n"
                "Use mock data where backend is unavailable — mark it with a comment."
            )

        return (
            "Generate a complete frontend reconstruction of the observed website.\n"
            "Create the following files:\n"
            "- index.html (main entry point)\n"
            "- styles.css (all styles, using extracted design tokens)\n"
            "- app.js (client-side behavior for all observed interactions)\n"
            "- README.md (brief project description and how to run)\n"
            "Use only observed evidence. Mark mock data with comments.\n"
            "Include the filename as a comment on the first line of each code block."
        )

    def _extract_and_write_files(self, text: str) -> list[str]:
        """Parse code blocks from AI response and write them to the workspace."""
        written: list[str] = []
        blocks = list(_FILE_BLOCK.finditer(text))

        if not blocks:
            return []

        for i, match in enumerate(blocks):
            code = match.group("code") or ""
            filename = match.group("filename")

            # Try to infer filename from first-line comment if not in opening fence.
            if not filename:
                fn_match = _FILENAME_COMMENT.search(code[:200])
                if fn_match:
                    filename = fn_match.group(1)

            # Fallback filename based on position and language hint.
            if not filename:
                lang = match.group(0).split("\n")[0].strip("`").lower()
                ext_map = {
                    "html": "html", "css": "css", "js": "js", "javascript": "js",
                    "jsx": "jsx", "ts": "ts", "tsx": "tsx", "json": "json",
                    "svelte": "svelte", "vue": "vue",
                }
                ext = ext_map.get(lang, "txt")
                filename = f"file_{i + 1:02d}.{ext}"

            # Security: flatten any path traversal.
            safe_name = _safe_relative_path(filename)
            if not safe_name:
                continue

            # Backup if file exists and has been modified.
            self.workspace.backup_if_exists(safe_name)
            self.workspace.write(safe_name, code)

            content_hash = hashlib.sha256(code.encode()).hexdigest()[:20]
            self.project_store.record_generated_file(
                self.project.id, safe_name,
                purpose=f"generated from reconstruction task",
                content_hash=content_hash,
            )
            written.append(safe_name)
            log.info("Generated: %s (%d chars)", safe_name, len(code))

        return written


def _safe_relative_path(path: str) -> str:
    """Sanitize a path from AI output — remove traversal attempts."""
    # Collapse any ../ sequences.
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != ".."]
    if not parts:
        return ""
    return "/".join(parts)
