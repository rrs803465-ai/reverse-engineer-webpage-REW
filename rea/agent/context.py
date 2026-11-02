"""Context manager: builds the smallest AI context that answers each request.

Never sends full conversation history with every call.
Each request gets: system prompt + compact project state + task-specific evidence.
"""

from __future__ import annotations

import logging
from typing import Any

from rea.analysis.evidence import build_page_evidence, build_project_summary
from rea.models import (
    AssetInfo, DiscoveredAction, NetworkEvent, PageInfo, Project,
)

log = logging.getLogger(__name__)

_MAX_HISTORY_TURNS = 6   # Only last N turns kept in context


class ContextManager:
    """Assembles efficient AI message lists for different task types."""

    SYSTEM_PROMPT = """You are REW — a website reverse-engineering agent.

Your job:
1. Analyze website structure, behavior, and design from provided evidence.
2. Identify client-side interactions and map them accurately.
3. Generate clean, working frontend reconstructions.
4. Use tools to gather evidence before drawing conclusions.

Rules:
- Use ONLY provided evidence. Do not invent unobserved functionality.
- Mark any mock or inferred data with a comment.
- Never expose secrets, credentials, or private user data.
- Be precise. If something is unknown, say so explicitly.
- Prefer minimal, maintainable code over complex abstractions.
- Call tools to gather more evidence before generating code when uncertain.
"""

    def __init__(
        self,
        project: Project,
        pages: list[PageInfo],
        actions: list[DiscoveredAction],
        network_events: list[NetworkEvent],
        assets: list[AssetInfo],
        design_tokens: dict[str, Any] | None = None,
        js_analysis: list[dict[str, Any]] | None = None,
    ) -> None:
        self.project = project
        self.pages = pages
        self.actions = actions
        self.network_events = network_events
        self.assets = assets
        self.design_tokens = design_tokens or {}
        self.js_analysis = js_analysis or []
        self._history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Message builders for different task types
    # ------------------------------------------------------------------

    def messages_for_analysis(self, task: str) -> list[dict[str, Any]]:
        """Build messages for a general analysis task."""
        project_ctx = build_project_summary(
            self.project,
            self.pages,
            self.actions,
            self.network_events,
            self.assets,
            self.design_tokens,
            self.js_analysis,
        )
        user_content = f"{project_ctx}\n\n---\n\n**Task:** {task}"
        return [{"role": "user", "content": user_content}]

    def messages_for_page_task(self, page_url: str, task: str) -> list[dict[str, Any]]:
        """Build messages focused on a single page."""
        page = next((p for p in self.pages if p.url == page_url), None)
        if page is None:
            return self.messages_for_analysis(f"[Page not found: {page_url}]\n{task}")

        page_ctx = build_page_evidence(
            page,
            self.actions,
            self.network_events,
        )
        user_content = f"{page_ctx}\n\n---\n\n**Task:** {task}"
        return [{"role": "user", "content": user_content}]

    def messages_for_reconstruction(
        self,
        task: str,
        generated_files: list[dict] | None = None,
        target_route: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build messages for code generation — includes design tokens and evidence."""
        from rea.analysis.evidence import build_reconstruction_brief

        brief = build_reconstruction_brief(
            self.project,
            self.pages,
            self.actions,
            self.design_tokens,
            generated_files or [],
            target_route=target_route,
        )
        user_content = f"{brief}\n\n---\n\n**Task:** {task}"
        return [{"role": "user", "content": user_content}]

    def messages_for_tool_loop(
        self,
        task: str,
        tool_results: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Build messages for an ongoing tool-calling loop with recent history."""
        messages: list[dict[str, Any]] = []

        # Compact project state as first user message.
        project_ctx = build_project_summary(
            self.project,
            self.pages[:5],   # fewer pages for tool-loop context
            self.actions,
            self.network_events,
            self.assets,
            self.design_tokens,
        )
        messages.append({"role": "user", "content": project_ctx})
        messages.append({
            "role": "assistant",
            "content": "Project context received. Ready to proceed.",
        })

        # Recent conversation history (bounded).
        recent = self._history[-(_MAX_HISTORY_TURNS * 2):]
        messages.extend(recent)

        # Current task.
        if tool_results:
            # Append tool results as assistant + tool_result pairs.
            for tr in tool_results:
                messages.append({
                    "role": "user",
                    "content": f"Tool result for `{tr['tool']}`:\n```json\n{tr['result_summary']}\n```",
                })

        messages.append({"role": "user", "content": f"**Current task:** {task}"})
        return messages

    def add_to_history(self, role: str, content: str) -> None:
        """Track conversation for multi-turn continuity (bounded)."""
        self._history.append({"role": role, "content": content})
        # Prune to keep memory bounded.
        if len(self._history) > _MAX_HISTORY_TURNS * 2 + 4:
            self._history = self._history[-((_MAX_HISTORY_TURNS * 2)):]

    def estimate_message_tokens(
        self, messages: list[dict[str, Any]], estimator: Any
    ) -> int:
        text = "\n".join(
            m.get("content", "") if isinstance(m.get("content"), str) else ""
            for m in messages
        )
        return estimator(text)
