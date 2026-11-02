"""Agent orchestrator: the tool-using execution loop.

Runs: observe → decide → call tool → record → repeat until done or budget hit.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

try:
    from rich.console import Console as _RichConsole
    _console = _RichConsole()
except ImportError:
    class _FallbackConsole:
        def print(self, *args: Any, **kwargs: Any) -> None:
            # Strip rich markup for plain output.
            import re
            text = " ".join(str(a) for a in args)
            text = re.sub(r"\[/?[^\]]+\]", "", text)
            print(text)
    _console = _FallbackConsole()

from rea.agent.context import ContextManager
from rea.agent.task_state import TaskState
from rea.agent.token_budget import BudgetExceededError, TokenBudget
from rea.agent.tool_registry import ToolCallError, ToolRegistry
from rea.models import UsageRecord
from rea.providers.base import ProviderBase
from rea.storage.usage import UsageStore

log = logging.getLogger(__name__)
console = _console

_MAX_TOOL_ROUNDS = 20    # Safety cap on tool-call rounds per task


class AgentOrchestrator:
    """Drives the AI agent through a structured tool-calling loop.

    Each call to ``run_task`` is independent and resumable: progress is
    persisted to ``TaskState`` before potentially-failing API requests.
    """

    def __init__(
        self,
        provider: ProviderBase,
        registry: ToolRegistry,
        context: ContextManager,
        budget: TokenBudget,
        task_state: TaskState,
        usage_store: UsageStore,
        project_id: str,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.context = context
        self.budget = budget
        self.task_state = task_state
        self.usage_store = usage_store
        self.project_id = project_id

    async def run_task(
        self,
        task_type: str,
        task_description: str,
        input_data: dict[str, Any] | None = None,
        max_tokens: int = 4096,
        use_tools: bool = True,
    ) -> dict[str, Any]:
        """Execute one agent task, returning a result dict.

        ``task_type`` is a stable identifier used for deduplication.
        If this task_type is already complete in this project, returns
        the cached result without spending tokens.
        """
        # Deduplication: skip if already done.
        completed = self.task_state.completed_task_types()
        if task_type in completed:
            log.info("Task '%s' already complete — skipping.", task_type)
            return {"status": "skipped", "reason": "already_complete"}

        task_id = self.task_state.create_task(task_type, input_data or {})
        console.print(f"[dim]Agent task:[/dim] [bold]{task_type}[/bold]")

        accumulated_tool_results: list[dict[str, Any]] = []
        final_text: str | None = None

        try:
            for round_num in range(_MAX_TOOL_ROUNDS):
                self.budget.check_all()
                self.budget.record_iteration()

                messages = self.context.messages_for_tool_loop(
                    task=task_description,
                    tool_results=accumulated_tool_results if accumulated_tool_results else None,
                )

                est_input = self.context.estimate_message_tokens(
                    messages, self.provider.estimate_tokens
                )
                self.budget.check_input(est_input)

                tools_schema = self.registry.to_openai_schemas() if use_tools else []

                if use_tools and tools_schema:
                    text, tool_calls, usage = await self.provider.generate_with_tools(
                        messages=messages,
                        tools=tools_schema,
                        system=ContextManager.SYSTEM_PROMPT,
                        max_tokens=max_tokens,
                    )
                else:
                    text, usage = await self.provider.generate_response(
                        messages=messages,
                        system=ContextManager.SYSTEM_PROMPT,
                        max_tokens=max_tokens,
                    )
                    tool_calls = []

                # Record usage.
                usage.project_id = self.project_id
                usage.task_label = task_type
                self.usage_store.record(usage)
                self.budget.record_request(
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.estimated_cost_usd,
                )

                if text:
                    self.context.add_to_history("assistant", text)
                    final_text = text

                # No tool calls → task is done.
                if not tool_calls:
                    log.debug("Task '%s' complete after %d rounds.", task_type, round_num + 1)
                    break

                # Execute tool calls.
                accumulated_tool_results = []
                for tc in tool_calls:
                    self.budget.check_tool_calls()
                    self.budget.record_tool_call()

                    tool_name = tc["name"]
                    tool_args = tc.get("arguments", {})

                    console.print(f"  [cyan]→ tool:[/cyan] {tool_name}({', '.join(f'{k}={repr(v)[:40]}' for k, v in tool_args.items())})")

                    try:
                        result = await self.registry.invoke(tool_name, tool_args)
                    except ToolCallError as exc:
                        result = {"error": str(exc)}

                    # Summarize result for context (never dump huge payloads back).
                    result_summary = _summarize_tool_result(result)
                    self.task_state.record_tool_call(task_id, tool_name, tool_args, result_summary)
                    accumulated_tool_results.append(
                        {"tool": tool_name, "result_summary": result_summary}
                    )

            else:
                log.warning("Task '%s' hit max tool rounds (%d).", task_type, _MAX_TOOL_ROUNDS)

        except BudgetExceededError as exc:
            log.warning("Budget exceeded during task '%s': %s", task_type, exc)
            self.task_state.fail_task(task_id, str(exc))
            return {"status": "budget_exceeded", "error": str(exc), "partial_text": final_text}

        except Exception as exc:
            log.error("Task '%s' failed: %s", task_type, exc, exc_info=True)
            self.task_state.fail_task(task_id, str(exc))
            return {"status": "error", "error": str(exc), "partial_text": final_text}

        result_data = {"status": "complete", "text": final_text}
        self.task_state.complete_task(task_id, result_data)
        return result_data


def _summarize_tool_result(result: Any) -> str:
    """Produce a bounded string summary of a tool result for AI context."""
    if isinstance(result, str):
        return result[:1500]
    if isinstance(result, dict):
        if "error" in result:
            return f"Error: {result['error']}"
        try:
            text = json.dumps(result, default=str, indent=2)
            return text[:1500] + (" [truncated]" if len(text) > 1500 else "")
        except Exception:
            return str(result)[:1500]
    return str(result)[:1500]
