"""Token budget enforcement — hard limits on input/output tokens and estimated cost."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


class BudgetExceededError(RuntimeError):
    """Raised when a configured budget limit is hit."""


@dataclass
class TokenBudget:
    """Tracks token consumption and enforces configured limits."""

    max_input_tokens: int = 500_000
    max_output_tokens: int = 100_000
    max_api_requests: int = 500
    max_cost_usd: float | None = None
    max_iterations: int = 50
    max_tool_calls: int = 100

    # Running totals.
    used_input: int = 0
    used_output: int = 0
    used_requests: int = 0
    used_cost: float = 0.0
    used_iterations: int = 0
    used_tool_calls: int = 0

    warnings_issued: set[str] = field(default_factory=set)

    # ---------------------------------------------------------------------------
    # Increment
    # ---------------------------------------------------------------------------

    def record_request(
        self,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None = None,
    ) -> None:
        self.used_input += input_tokens
        self.used_output += output_tokens
        self.used_requests += 1
        if cost_usd is not None:
            self.used_cost += cost_usd
        self._warn_near_limits()

    def record_tool_call(self) -> None:
        self.used_tool_calls += 1

    def record_iteration(self) -> None:
        self.used_iterations += 1

    # ---------------------------------------------------------------------------
    # Guard checks — call before making a request
    # ---------------------------------------------------------------------------

    def check_input(self, estimated_tokens: int) -> None:
        """Raise if adding *estimated_tokens* would exceed the input budget."""
        if self.used_input + estimated_tokens > self.max_input_tokens:
            raise BudgetExceededError(
                f"Input token budget exceeded: used {self.used_input:,}, "
                f"requesting ~{estimated_tokens:,}, limit {self.max_input_tokens:,}."
            )

    def check_output(self, requested_tokens: int) -> None:
        if self.used_output + requested_tokens > self.max_output_tokens:
            raise BudgetExceededError(
                f"Output token budget exceeded: used {self.used_output:,}, "
                f"requesting {requested_tokens:,}, limit {self.max_output_tokens:,}."
            )

    def check_requests(self) -> None:
        if self.used_requests >= self.max_api_requests:
            raise BudgetExceededError(
                f"API request limit reached: {self.used_requests} / {self.max_api_requests}."
            )

    def check_cost(self, estimated_cost: float | None = None) -> None:
        if self.max_cost_usd is None:
            return
        total = self.used_cost + (estimated_cost or 0.0)
        if total > self.max_cost_usd:
            raise BudgetExceededError(
                f"Cost budget exceeded: used ${self.used_cost:.4f}, "
                f"limit ${self.max_cost_usd:.4f}."
            )

    def check_iterations(self) -> None:
        if self.used_iterations >= self.max_iterations:
            raise BudgetExceededError(
                f"Agent iteration limit reached: {self.used_iterations} / {self.max_iterations}."
            )

    def check_tool_calls(self) -> None:
        if self.used_tool_calls >= self.max_tool_calls:
            raise BudgetExceededError(
                f"Tool call limit reached: {self.used_tool_calls} / {self.max_tool_calls}."
            )

    def check_all(self, est_input: int = 0, est_output: int = 0) -> None:
        """Convenience: run all checks before issuing a request."""
        self.check_iterations()
        self.check_requests()
        self.check_tool_calls()
        if est_input:
            self.check_input(est_input)
        if est_output:
            self.check_output(est_output)
        self.check_cost()

    # ---------------------------------------------------------------------------
    # Reporting
    # ---------------------------------------------------------------------------

    def remaining_input(self) -> int:
        return max(0, self.max_input_tokens - self.used_input)

    def remaining_output(self) -> int:
        return max(0, self.max_output_tokens - self.used_output)

    def summary(self) -> dict[str, object]:
        return {
            "input_tokens": {"used": self.used_input, "limit": self.max_input_tokens},
            "output_tokens": {"used": self.used_output, "limit": self.max_output_tokens},
            "api_requests": {"used": self.used_requests, "limit": self.max_api_requests},
            "iterations": {"used": self.used_iterations, "limit": self.max_iterations},
            "tool_calls": {"used": self.used_tool_calls, "limit": self.max_tool_calls},
            "estimated_cost_usd": round(self.used_cost, 6),
            "cost_limit_usd": self.max_cost_usd,
        }

    # ---------------------------------------------------------------------------
    # Internal
    # ---------------------------------------------------------------------------

    def _warn_near_limits(self) -> None:
        def _warn(key: str, used: int | float, limit: int | float, label: str) -> None:
            if limit <= 0:
                return
            ratio = used / limit
            if ratio >= 0.9 and key not in self.warnings_issued:
                self.warnings_issued.add(key)
                log.warning(
                    "Budget warning: %s at %.0f%% (%s / %s).",
                    label, ratio * 100, used, limit,
                )

        _warn("input", self.used_input, self.max_input_tokens, "input tokens")
        _warn("output", self.used_output, self.max_output_tokens, "output tokens")
        _warn("requests", self.used_requests, self.max_api_requests, "API requests")
        if self.max_cost_usd:
            _warn("cost", self.used_cost, self.max_cost_usd, "estimated cost")
