from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from agent_quality.paths import default_db_path, ensure_home
from agent_quality.timeutil import utc_now

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    repository_path TEXT NOT NULL,
    repository_remote_hash TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    final_outcome TEXT,
    task_summary TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    turn_number INTEGER NOT NULL DEFAULT 1,
    prompt TEXT,
    prompt_hash TEXT NOT NULL,
    repository_path TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    resulting_commit TEXT,
    model TEXT,
    agent_adapter TEXT NOT NULL,
    agent_version TEXT,
    wrapper_version TEXT,
    codex_config_hash TEXT,
    agents_md_hash TEXT,
    verifier_version TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_ms INTEGER,
    agent_status TEXT NOT NULL,
    verifier_status TEXT,
    human_status TEXT,
    lifecycle_status TEXT,
    input_tokens INTEGER,
    cached_input_tokens INTEGER,
    output_tokens INTEGER,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    source_product TEXT,
    source_event_type TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    session_id TEXT,
    run_id TEXT,
    turn_id TEXT,
    parent_event_id TEXT,
    sequence_number INTEGER,
    occurred_at TEXT,
    observed_at TEXT NOT NULL,
    status TEXT,
    item_type TEXT,
    tool_category TEXT,
    command TEXT,
    exit_code INTEGER,
    path TEXT,
    duration_ms INTEGER,
    normalized_payload TEXT,
    source_payload_sanitized TEXT NOT NULL,
    provider_extensions TEXT,
    privacy_status TEXT NOT NULL,
    privacy_policy_version TEXT NOT NULL,
    redaction_findings TEXT,
    normalization_status TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE TABLE IF NOT EXISTS verifier_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    verifier_name TEXT NOT NULL,
    verifier_category TEXT NOT NULL,
    command TEXT,
    started_at TEXT,
    duration_ms INTEGER,
    exit_code INTEGER,
    passed INTEGER NOT NULL,
    stdout_path TEXT,
    stderr_path TEXT,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE TABLE IF NOT EXISTS human_reviews (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    code_retention TEXT,
    severity TEXT,
    primary_failure_category TEXT,
    contributing_categories TEXT,
    confidence REAL,
    critical_event_sequence INTEGER,
    notes TEXT,
    reviewed_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(id)
);
CREATE TABLE IF NOT EXISTS failure_clusters (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    primary_category TEXT,
    severity TEXT,
    status TEXT NOT NULL,
    first_seen_at TEXT,
    last_seen_at TEXT,
    occurrence_count INTEGER DEFAULT 0,
    proposed_intervention TEXT,
    linked_regression_case TEXT
);
CREATE TABLE IF NOT EXISTS provider_artifacts (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    run_id TEXT,
    created_by_turn_id TEXT,
    source_provider TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    title TEXT,
    approval_status TEXT,
    current_revision_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS provider_artifact_revisions (
    id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    created_by_event_id TEXT,
    revision_number INTEGER NOT NULL,
    payload_sanitized TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(artifact_id) REFERENCES provider_artifacts(id)
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    ensure_home()
    conn = sqlite3.connect(path or default_db_path())
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def insert(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> None:
    columns = list(values)
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        [values[column] for column in columns],
    )


def update_run(conn: sqlite3.Connection, run_id: str, **values: Any) -> None:
    if not values:
        return
    assignments = ", ".join(f"{column}=?" for column in values)
    conn.execute(f"UPDATE runs SET {assignments} WHERE id=?", [*values.values(), run_id])


def one(conn: sqlite3.Connection, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
    return conn.execute(sql, tuple(args)).fetchone()


def all_rows(conn: sqlite3.Connection, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return list(conn.execute(sql, tuple(args)))


def json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)


def mark_session_ended(conn: sqlite3.Connection, session_id: str, outcome: str | None = None) -> None:
    conn.execute(
        "UPDATE sessions SET ended_at=?, final_outcome=COALESCE(?, final_outcome) WHERE id=?",
        (utc_now(), outcome, session_id),
    )
