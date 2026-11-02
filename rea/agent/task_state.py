"""Persistent agent task state — survives restarts and API failures."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from rea.storage.database import Database

log = logging.getLogger(__name__)


class TaskState:
    """Records agent tasks and their tool-call history in SQLite."""

    def __init__(self, db: Database, project_id: str) -> None:
        self.db = db
        self.project_id = project_id

    def create_task(self, task_type: str, input_data: dict[str, Any]) -> str:
        task_id = uuid.uuid4().hex[:16]
        now = datetime.utcnow().isoformat()
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO agent_tasks
                   (id, project_id, task_type, status, input_json,
                    tool_calls_json, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    task_id,
                    self.project_id,
                    task_type,
                    "running",
                    json.dumps(input_data),
                    "[]",
                    now,
                ),
            )
        return task_id

    def record_tool_call(
        self,
        task_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
    ) -> None:
        row = self.db.fetchone(
            "SELECT tool_calls_json FROM agent_tasks WHERE id = ?", (task_id,)
        )
        if not row:
            return
        calls: list[dict[str, Any]] = json.loads(row["tool_calls_json"] or "[]")
        calls.append({
            "tool": tool_name,
            "arguments": arguments,
            "result": result,
            "at": datetime.utcnow().isoformat(),
        })
        with self.db.transaction():
            self.db.execute(
                "UPDATE agent_tasks SET tool_calls_json = ? WHERE id = ?",
                (json.dumps(calls), task_id),
            )

    def complete_task(self, task_id: str, result: Any) -> None:
        with self.db.transaction():
            self.db.execute(
                """UPDATE agent_tasks
                   SET status = 'complete', result_json = ?, completed_at = ?
                   WHERE id = ?""",
                (json.dumps(result), datetime.utcnow().isoformat(), task_id),
            )

    def fail_task(self, task_id: str, error: str) -> None:
        with self.db.transaction():
            self.db.execute(
                """UPDATE agent_tasks
                   SET status = 'error', error_message = ?, completed_at = ?
                   WHERE id = ?""",
                (error[:500], datetime.utcnow().isoformat(), task_id),
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone(
            "SELECT * FROM agent_tasks WHERE id = ?", (task_id,)
        )
        if not row:
            return None
        return {
            "id": row["id"],
            "task_type": row["task_type"],
            "status": row["status"],
            "input": json.loads(row["input_json"] or "{}"),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error_message"],
            "tool_calls": json.loads(row["tool_calls_json"] or "[]"),
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
        }

    def pending_tasks(self) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM agent_tasks WHERE project_id = ? AND status = 'running'",
            (self.project_id,),
        )
        return [
            {
                "id": r["id"],
                "task_type": r["task_type"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def completed_task_types(self) -> set[str]:
        rows = self.db.fetchall(
            "SELECT DISTINCT task_type FROM agent_tasks WHERE project_id = ? AND status = 'complete'",
            (self.project_id,),
        )
        return {r["task_type"] for r in rows}
