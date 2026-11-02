"""SQLite database setup and connection management for REW."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

log = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    target_url  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'created',
    provider_name TEXT,
    model_id    TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    workspace_path TEXT,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS pages (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    url             TEXT NOT NULL,
    canonical_url   TEXT,
    title           TEXT,
    status_code     INTEGER,
    content_type    TEXT,
    headings_json   TEXT NOT NULL DEFAULT '[]',
    links_json      TEXT NOT NULL DEFAULT '[]',
    forms_json      TEXT NOT NULL DEFAULT '[]',
    meta_json       TEXT NOT NULL DEFAULT '{}',
    screenshot_path TEXT,
    console_errors_json TEXT NOT NULL DEFAULT '[]',
    failed_requests_json TEXT NOT NULL DEFAULT '[]',
    scanned_at      TEXT NOT NULL,
    UNIQUE(project_id, url)
);

CREATE TABLE IF NOT EXISTS actions (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    page_url        TEXT NOT NULL,
    element_type    TEXT NOT NULL,
    label           TEXT,
    selector        TEXT,
    aria_label      TEXT,
    initial_state   TEXT,
    observed_result TEXT,
    url_changed     INTEGER NOT NULL DEFAULT 0,
    network_activity INTEGER NOT NULL DEFAULT 0,
    confidence      TEXT NOT NULL DEFAULT 'unknown',
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS network_events (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    page_url        TEXT,
    method          TEXT NOT NULL,
    url             TEXT NOT NULL,
    resource_type   TEXT,
    status          INTEGER,
    content_type    TEXT,
    duration_ms     REAL,
    is_xhr          INTEGER NOT NULL DEFAULT 0,
    is_websocket    INTEGER NOT NULL DEFAULT 0,
    request_summary TEXT,
    response_summary TEXT,
    associated_action_id TEXT
);

CREATE TABLE IF NOT EXISTS assets (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    url             TEXT NOT NULL,
    asset_type      TEXT NOT NULL,
    file_size_bytes INTEGER,
    content_hash    TEXT,
    local_path      TEXT,
    extracted_vars_json TEXT NOT NULL DEFAULT '{}',
    fonts_json      TEXT NOT NULL DEFAULT '[]',
    colors_json     TEXT NOT NULL DEFAULT '[]',
    UNIQUE(project_id, url)
);

CREATE TABLE IF NOT EXISTS usage_records (
    id              TEXT PRIMARY KEY,
    project_id      TEXT,
    provider        TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cached_tokens   INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    api_requests    INTEGER NOT NULL DEFAULT 1,
    retried_requests INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL,
    source          TEXT NOT NULL DEFAULT 'api',
    task_label      TEXT
);

CREATE TABLE IF NOT EXISTS agent_tasks (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    task_type       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    input_json      TEXT NOT NULL DEFAULT '{}',
    result_json     TEXT,
    error_message   TEXT,
    tool_calls_json TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS generated_files (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    relative_path   TEXT NOT NULL,
    purpose         TEXT,
    content_hash    TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(project_id, relative_path)
);
"""


def _db_path(workspace: Path | None = None) -> Path:
    base = workspace or (Path.home() / ".local" / "share" / "rew")
    base.mkdir(parents=True, exist_ok=True)
    return base / "rew.db"


class Database:
    """Thin wrapper around a SQLite connection with schema management."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _db_path()
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, params_seq: list) -> sqlite3.Cursor:
        return self.conn.executemany(sql, params_seq)

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def _migrate(self) -> None:
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        has_version_table = cur.fetchone()[0] > 0

        if not has_version_table:
            self.conn.executescript(_DDL)
            self.conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (_SCHEMA_VERSION,)
            )
            self.conn.commit()
            log.debug("REW database initialized at %s", self.path)
            return

        row = self.conn.execute("SELECT version FROM schema_version").fetchone()
        current = row[0] if row else 0
        if current < _SCHEMA_VERSION:
            log.info("Migrating database from version %d to %d.", current, _SCHEMA_VERSION)
            self.conn.executescript(_DDL)
            self.conn.execute("UPDATE schema_version SET version = ?", (_SCHEMA_VERSION,))
            self.conn.commit()


# Global database instance (initialized lazily).
_db: Database | None = None


def get_db(path: Path | None = None) -> Database:
    global _db
    if _db is None:
        _db = Database(path)
        _db.connect()
    return _db
