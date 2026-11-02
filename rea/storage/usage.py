"""Token usage and cost accounting persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from rea.models import UsageRecord
from rea.storage.database import Database

# Approximate pricing (USD per million tokens) for common models.
# Users can override; these are fallback estimates only.
_PRICE_TABLE: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
    "claude-opus": {"input": 15.00, "output": 75.00},
}


def _estimate_cost(model_id: str, input_tokens: int, output_tokens: int) -> float | None:
    for key, prices in _PRICE_TABLE.items():
        if key in model_id.lower():
            cost = (input_tokens / 1_000_000) * prices["input"]
            cost += (output_tokens / 1_000_000) * prices["output"]
            return round(cost, 6)
    return None


class UsageStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(self, usage: UsageRecord) -> None:
        if usage.estimated_cost_usd is None:
            usage.estimated_cost_usd = _estimate_cost(
                usage.model_id, usage.input_tokens, usage.output_tokens
            )
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO usage_records
                   (id, project_id, provider, model_id, timestamp,
                    input_tokens, output_tokens, cached_tokens, reasoning_tokens,
                    api_requests, retried_requests, estimated_cost_usd, source, task_label)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    usage.id,
                    usage.project_id,
                    usage.provider,
                    usage.model_id,
                    usage.timestamp.isoformat(),
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cached_tokens,
                    usage.reasoning_tokens,
                    usage.api_requests,
                    usage.retried_requests,
                    usage.estimated_cost_usd,
                    usage.source,
                    usage.task_label,
                ),
            )

    def summary(self, project_id: str | None = None) -> dict[str, Any]:
        where = "WHERE project_id = ?" if project_id else ""
        params = (project_id,) if project_id else ()
        row = self.db.fetchone(
            f"""SELECT
                SUM(input_tokens)       AS total_input,
                SUM(output_tokens)      AS total_output,
                SUM(cached_tokens)      AS total_cached,
                SUM(api_requests)       AS total_requests,
                SUM(retried_requests)   AS total_retries,
                SUM(estimated_cost_usd) AS total_cost,
                COUNT(*)                AS record_count,
                MAX(provider)           AS last_provider,
                MAX(model_id)           AS last_model
            FROM usage_records {where}""",
            params,
        )
        if not row:
            return {}
        return {
            "total_input_tokens": row["total_input"] or 0,
            "total_output_tokens": row["total_output"] or 0,
            "total_cached_tokens": row["total_cached"] or 0,
            "total_api_requests": row["total_requests"] or 0,
            "total_retried_requests": row["total_retries"] or 0,
            "estimated_cost_usd": round(row["total_cost"] or 0.0, 6),
            "record_count": row["record_count"] or 0,
            "provider": row["last_provider"],
            "model_id": row["last_model"],
        }

    def totals_by_project(self) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            """SELECT project_id,
               SUM(input_tokens) AS inp,
               SUM(output_tokens) AS out,
               SUM(estimated_cost_usd) AS cost
               FROM usage_records
               GROUP BY project_id
               ORDER BY cost DESC"""
        )
        return [dict(r) for r in rows]
