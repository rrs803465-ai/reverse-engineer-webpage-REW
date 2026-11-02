"""Project, page, action, and asset persistence."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from rea.models import (
    AssetInfo, DiscoveredAction, NetworkEvent,
    PageInfo, Project, ProjectConfig, ProjectStatus,
)
from rea.storage.database import Database


def _now() -> str:
    return datetime.utcnow().isoformat()


class ProjectStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def create(self, project: Project) -> Project:
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO projects
                   (id, name, target_url, status, provider_name, model_id,
                    config_json, created_at, updated_at, workspace_path, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    project.id,
                    project.name,
                    project.target_url,
                    project.status.value,
                    project.provider_name,
                    project.model_id,
                    project.config.model_dump_json(),
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                    project.workspace_path,
                    project.notes,
                ),
            )
        return project

    def get(self, project_id: str) -> Project | None:
        row = self.db.fetchone(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        )
        return self._row_to_project(row) if row else None

    def list_all(self) -> list[Project]:
        rows = self.db.fetchall(
            "SELECT * FROM projects ORDER BY updated_at DESC"
        )
        return [self._row_to_project(r) for r in rows]

    def update_status(self, project_id: str, status: ProjectStatus) -> None:
        with self.db.transaction():
            self.db.execute(
                "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
                (status.value, _now(), project_id),
            )

    def update(self, project: Project) -> None:
        project.updated_at = datetime.utcnow()
        with self.db.transaction():
            self.db.execute(
                """UPDATE projects SET
                   name=?, status=?, provider_name=?, model_id=?,
                   config_json=?, updated_at=?, workspace_path=?, notes=?
                   WHERE id=?""",
                (
                    project.name,
                    project.status.value,
                    project.provider_name,
                    project.model_id,
                    project.config.model_dump_json(),
                    project.updated_at.isoformat(),
                    project.workspace_path,
                    project.notes,
                    project.id,
                ),
            )

    def delete(self, project_id: str) -> None:
        with self.db.transaction():
            self.db.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    @staticmethod
    def _row_to_project(row: Any) -> Project:
        try:
            config = ProjectConfig(**json.loads(row["config_json"] or "{}"))
        except Exception:
            config = ProjectConfig()
        return Project(
            id=row["id"],
            name=row["name"],
            target_url=row["target_url"],
            status=ProjectStatus(row["status"]),
            provider_name=row["provider_name"],
            model_id=row["model_id"],
            config=config,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            workspace_path=row["workspace_path"],
            notes=row["notes"],
        )

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    def upsert_page(self, project_id: str, page: PageInfo) -> None:
        page_id = uuid.uuid4().hex
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO pages
                   (id, project_id, url, canonical_url, title, status_code,
                    content_type, headings_json, links_json, forms_json, meta_json,
                    screenshot_path, console_errors_json, failed_requests_json, scanned_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(project_id, url) DO UPDATE SET
                   canonical_url=excluded.canonical_url, title=excluded.title,
                   status_code=excluded.status_code, content_type=excluded.content_type,
                   headings_json=excluded.headings_json, links_json=excluded.links_json,
                   forms_json=excluded.forms_json, meta_json=excluded.meta_json,
                   screenshot_path=excluded.screenshot_path,
                   console_errors_json=excluded.console_errors_json,
                   failed_requests_json=excluded.failed_requests_json,
                   scanned_at=excluded.scanned_at""",
                (
                    page_id,
                    project_id,
                    page.url,
                    page.canonical_url,
                    page.title,
                    page.status_code,
                    page.content_type,
                    json.dumps(page.headings),
                    json.dumps(page.links),
                    json.dumps(page.forms),
                    json.dumps(page.meta),
                    page.screenshot_path,
                    json.dumps(page.console_errors),
                    json.dumps(page.failed_requests),
                    page.scanned_at.isoformat(),
                ),
            )

    def get_pages(self, project_id: str) -> list[PageInfo]:
        rows = self.db.fetchall(
            "SELECT * FROM pages WHERE project_id = ? ORDER BY scanned_at", (project_id,)
        )
        return [self._row_to_page(r) for r in rows]

    def page_urls(self, project_id: str) -> set[str]:
        rows = self.db.fetchall(
            "SELECT url FROM pages WHERE project_id = ?", (project_id,)
        )
        return {r["url"] for r in rows}

    @staticmethod
    def _row_to_page(row: Any) -> PageInfo:
        return PageInfo(
            url=row["url"],
            canonical_url=row["canonical_url"],
            title=row["title"],
            status_code=row["status_code"],
            content_type=row["content_type"],
            headings=json.loads(row["headings_json"] or "[]"),
            links=json.loads(row["links_json"] or "[]"),
            forms=json.loads(row["forms_json"] or "[]"),
            meta=json.loads(row["meta_json"] or "{}"),
            screenshot_path=row["screenshot_path"],
            console_errors=json.loads(row["console_errors_json"] or "[]"),
            failed_requests=json.loads(row["failed_requests_json"] or "[]"),
            scanned_at=datetime.fromisoformat(row["scanned_at"]),
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def save_action(self, project_id: str, action: DiscoveredAction) -> None:
        with self.db.transaction():
            self.db.execute(
                """INSERT OR REPLACE INTO actions
                   (id, project_id, page_url, element_type, label, selector,
                    aria_label, initial_state, observed_result, url_changed,
                    network_activity, confidence, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    action.id, project_id, action.page_url, action.element_type,
                    action.label, action.selector, action.aria_label,
                    action.initial_state, action.observed_result,
                    int(action.url_changed), int(action.network_activity),
                    action.confidence.value, action.notes,
                ),
            )

    def get_actions(self, project_id: str) -> list[DiscoveredAction]:
        rows = self.db.fetchall(
            "SELECT * FROM actions WHERE project_id = ?", (project_id,)
        )
        from rea.models import ActionConfidence
        result = []
        for r in rows:
            result.append(DiscoveredAction(
                id=r["id"],
                page_url=r["page_url"],
                element_type=r["element_type"],
                label=r["label"],
                selector=r["selector"],
                aria_label=r["aria_label"],
                initial_state=r["initial_state"],
                observed_result=r["observed_result"],
                url_changed=bool(r["url_changed"]),
                network_activity=bool(r["network_activity"]),
                confidence=ActionConfidence(r["confidence"]),
                notes=r["notes"],
            ))
        return result

    # ------------------------------------------------------------------
    # Network events
    # ------------------------------------------------------------------

    def save_network_events(self, project_id: str, events: list[NetworkEvent]) -> None:
        with self.db.transaction():
            for ev in events:
                self.db.execute(
                    """INSERT INTO network_events
                       (id, project_id, page_url, method, url, resource_type,
                        status, content_type, duration_ms, is_xhr, is_websocket,
                        request_summary, response_summary, associated_action_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        uuid.uuid4().hex, project_id, None,
                        ev.method, ev.url, ev.resource_type,
                        ev.status, ev.content_type, ev.duration_ms,
                        int(ev.is_xhr), int(ev.is_websocket),
                        ev.request_body_summary, ev.response_body_summary,
                        ev.associated_action_id,
                    ),
                )

    def get_network_events(self, project_id: str) -> list[NetworkEvent]:
        rows = self.db.fetchall(
            "SELECT * FROM network_events WHERE project_id = ?", (project_id,)
        )
        result = []
        for r in rows:
            result.append(NetworkEvent(
                method=r["method"],
                url=r["url"],
                resource_type=r["resource_type"],
                status=r["status"],
                content_type=r["content_type"],
                duration_ms=r["duration_ms"],
                is_xhr=bool(r["is_xhr"]),
                is_websocket=bool(r["is_websocket"]),
                request_body_summary=r["request_summary"],
                response_body_summary=r["response_summary"],
                associated_action_id=r["associated_action_id"],
            ))
        return result

    # ------------------------------------------------------------------
    # Assets
    # ------------------------------------------------------------------

    def upsert_asset(self, project_id: str, asset: AssetInfo) -> None:
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO assets
                   (id, project_id, url, asset_type, file_size_bytes, content_hash,
                    local_path, extracted_vars_json, fonts_json, colors_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(project_id, url) DO UPDATE SET
                   asset_type=excluded.asset_type,
                   file_size_bytes=excluded.file_size_bytes,
                   content_hash=excluded.content_hash,
                   local_path=excluded.local_path,
                   extracted_vars_json=excluded.extracted_vars_json,
                   fonts_json=excluded.fonts_json,
                   colors_json=excluded.colors_json""",
                (
                    uuid.uuid4().hex, project_id, asset.url,
                    asset.asset_type, asset.file_size_bytes, asset.content_hash,
                    asset.local_path,
                    json.dumps(asset.extracted_vars),
                    json.dumps(asset.fonts),
                    json.dumps(asset.colors),
                ),
            )

    def get_assets(self, project_id: str) -> list[AssetInfo]:
        rows = self.db.fetchall(
            "SELECT * FROM assets WHERE project_id = ?", (project_id,)
        )
        result = []
        for r in rows:
            result.append(AssetInfo(
                url=r["url"],
                asset_type=r["asset_type"],
                file_size_bytes=r["file_size_bytes"],
                content_hash=r["content_hash"],
                local_path=r["local_path"],
                extracted_vars=json.loads(r["extracted_vars_json"] or "{}"),
                fonts=json.loads(r["fonts_json"] or "[]"),
                colors=json.loads(r["colors_json"] or "[]"),
            ))
        return result

    # ------------------------------------------------------------------
    # Generated files manifest
    # ------------------------------------------------------------------

    def record_generated_file(
        self, project_id: str, relative_path: str, purpose: str, content_hash: str
    ) -> None:
        with self.db.transaction():
            self.db.execute(
                """INSERT INTO generated_files
                   (id, project_id, relative_path, purpose, content_hash, created_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(project_id, relative_path) DO UPDATE SET
                   purpose=excluded.purpose, content_hash=excluded.content_hash,
                   created_at=excluded.created_at""",
                (uuid.uuid4().hex, project_id, relative_path, purpose, content_hash, _now()),
            )

    def get_generated_files(self, project_id: str) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT relative_path, purpose, content_hash, created_at FROM generated_files WHERE project_id = ?",
            (project_id,),
        )
        return [dict(r) for r in rows]
