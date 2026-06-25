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
    linked_regression_case TEXT,
    provider_extensions TEXT
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
CREATE TABLE IF NOT EXISTS analysis_runs (
    id TEXT PRIMARY KEY,
    algorithm TEXT NOT NULL,
    parameters TEXT,
    judge_version TEXT,
    redaction_version TEXT,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS failure_cluster_memberships (
    analysis_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    assignment_type TEXT NOT NULL,
    confidence REAL,
    PRIMARY KEY (analysis_id, run_id, cluster_id),
    FOREIGN KEY(analysis_id) REFERENCES analysis_runs(id) ON DELETE CASCADE,
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE,
    FOREIGN KEY(cluster_id) REFERENCES failure_clusters(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS failure_instances (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    cluster_id TEXT,
    category TEXT,
    subcategory TEXT,
    description TEXT NOT NULL,
    severity TEXT NOT NULL,
    probable_cause TEXT,
    suggested_fix TEXT,
    affected_prompt_component TEXT,
    timestamp TEXT NOT NULL,
    llm_judge_score REAL,
    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE,
    FOREIGN KEY(cluster_id) REFERENCES failure_clusters(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_session_id ON runs (session_id);
CREATE INDEX IF NOT EXISTS idx_events_run_id ON events (run_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_run_id ON artifacts (run_id);
CREATE INDEX IF NOT EXISTS idx_verifier_results_run_id ON verifier_results (run_id);
CREATE INDEX IF NOT EXISTS idx_human_reviews_run_id ON human_reviews (run_id);
CREATE INDEX IF NOT EXISTS idx_provider_artifact_revisions_artifact_id ON provider_artifact_revisions (artifact_id);
CREATE INDEX IF NOT EXISTS idx_failure_instances_run_id ON failure_instances (run_id);
CREATE INDEX IF NOT EXISTS idx_failure_instances_cluster_id ON failure_instances (cluster_id);

"""

TABLE_SCHEMAS: dict[str, frozenset[str]] = {
    "sessions": frozenset(
        {
            "id",
            "repository_path",
            "repository_remote_hash",
            "started_at",
            "ended_at",
            "final_outcome",
            "task_summary",
        }
    ),
    "runs": frozenset(
        {
            "id",
            "session_id",
            "turn_number",
            "prompt",
            "prompt_hash",
            "repository_path",
            "base_commit",
            "resulting_commit",
            "model",
            "agent_adapter",
            "agent_version",
            "wrapper_version",
            "codex_config_hash",
            "agents_md_hash",
            "verifier_version",
            "started_at",
            "completed_at",
            "duration_ms",
            "agent_status",
            "verifier_status",
            "human_status",
            "lifecycle_status",
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
        }
    ),
    "events": frozenset(
        {
            "id",
            "schema_version",
            "event_type",
            "source_provider",
            "source_product",
            "source_event_type",
            "adapter_version",
            "session_id",
            "run_id",
            "turn_id",
            "parent_event_id",
            "sequence_number",
            "occurred_at",
            "observed_at",
            "status",
            "item_type",
            "tool_category",
            "command",
            "exit_code",
            "path",
            "duration_ms",
            "normalized_payload",
            "source_payload_sanitized",
            "provider_extensions",
            "privacy_status",
            "privacy_policy_version",
            "redaction_findings",
            "normalization_status",
            "idempotency_key",
        }
    ),
    "artifacts": frozenset({"id", "run_id", "artifact_type", "path", "sha256", "size_bytes"}),
    "verifier_results": frozenset(
        {
            "id",
            "run_id",
            "verifier_name",
            "verifier_category",
            "command",
            "started_at",
            "duration_ms",
            "exit_code",
            "passed",
            "stdout_path",
            "stderr_path",
        }
    ),
    "human_reviews": frozenset(
        {
            "id",
            "run_id",
            "outcome",
            "code_retention",
            "severity",
            "primary_failure_category",
            "contributing_categories",
            "confidence",
            "critical_event_sequence",
            "notes",
            "reviewed_at",
        }
    ),
    "failure_clusters": frozenset(
        {
            "id",
            "title",
            "description",
            "primary_category",
            "severity",
            "status",
            "first_seen_at",
            "last_seen_at",
            "occurrence_count",
            "proposed_intervention",
            "linked_regression_case",
            "provider_extensions",
        }
    ),
    "provider_artifacts": frozenset(
        {
            "id",
            "session_id",
            "run_id",
            "created_by_turn_id",
            "source_provider",
            "artifact_type",
            "title",
            "approval_status",
            "current_revision_id",
            "created_at",
            "updated_at",
        }
    ),
    "provider_artifact_revisions": frozenset(
        {
            "id",
            "artifact_id",
            "created_by_event_id",
            "revision_number",
            "payload_sanitized",
            "sha256",
            "created_at",
        }
    ),
    "analysis_runs": frozenset(
        {
            "id",
            "algorithm",
            "parameters",
            "judge_version",
            "redaction_version",
            "created_at",
            "status",
        }
    ),
    "failure_cluster_memberships": frozenset(
        {
            "analysis_id",
            "run_id",
            "cluster_id",
            "assignment_type",
            "confidence",
        }
    ),
    "failure_instances": frozenset(
        {
            "id",
            "run_id",
            "cluster_id",
            "category",
            "subcategory",
            "description",
            "severity",
            "probable_cause",
            "suggested_fix",
            "affected_prompt_component",
            "timestamp",
            "llm_judge_score",
        }
    ),
}


def _auto_redact(val: Any) -> Any:
    if isinstance(val, str):
        if (val.startswith("{") and val.endswith("}")) or (val.startswith("[") and val.endswith("]")):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, (dict, list)):
                    from agent_quality.privacy.redaction import redact_json
                    return json.dumps(redact_json(parsed).value, sort_keys=True)
            except Exception:
                pass
        from agent_quality.privacy.redaction import redact_text
        return redact_text(val).value
    elif isinstance(val, (dict, list)):
        from agent_quality.privacy.redaction import redact_json
        return redact_json(val).value
    return val


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    ensure_home()
    conn = sqlite3.connect(path or default_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _ensure_schema(conn)
    return conn


def insert(conn: sqlite3.Connection, table: str, values: dict[str, Any], or_action: str | None = None) -> None:
    columns = list(values)
    _validate_table_columns(table, columns)
    
    # Auto-redact string values to prevent leakage
    redacted_values = {col: _auto_redact(val) for col, val in values.items()}
    
    placeholders = ", ".join("?" for _ in columns)
    prefix = f"INSERT {or_action}" if or_action else "INSERT"
    conn.execute(
        f"{prefix} INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        [redacted_values[column] for column in columns],
    )


def update_run(conn: sqlite3.Connection, run_id: str, **values: Any) -> None:
    if not values:
        return
    _validate_table_columns("runs", values)
    
    # Auto-redact string values
    redacted_values = {col: _auto_redact(val) for col, val in values.items()}
    
    assignments = ", ".join(f"{column}=?" for column in redacted_values)
    conn.execute(f"UPDATE runs SET {assignments} WHERE id=?", [*redacted_values.values(), run_id])


def _validate_table_columns(table: str, columns: Iterable[str]) -> None:
    allowed = TABLE_SCHEMAS.get(table)
    if allowed is None:
        raise ValueError(f"unknown table: {table}")
    columns = list(columns)
    if not columns:
        raise ValueError(f"no columns supplied for {table}")
    invalid = [column for column in columns if column not in allowed]
    if invalid:
        raise ValueError(f"invalid columns for {table}: {', '.join(invalid)}")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    for statement in SCHEMA.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)
            
    # Idempotent schema migrations: check for failure_clusters column provider_extensions
    cursor = conn.execute("PRAGMA table_info(failure_clusters)")
    columns = [row["name"] for row in cursor.fetchall()]
    if columns and "provider_extensions" not in columns:
        conn.execute("ALTER TABLE failure_clusters ADD COLUMN provider_extensions TEXT;")



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
