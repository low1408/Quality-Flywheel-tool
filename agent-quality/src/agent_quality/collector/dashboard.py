from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from agent_quality.adapters.registry import (
    HookProviderAdapter,
    hook_context,
    hook_context_from_source,
)
from agent_quality.db import all_rows, connect, insert, one


DASHBOARD_TEXT_LIMIT = 12_000
DASHBOARD_OUTPUT_LIMIT = 60_000
DASHBOARD_LIST_LIMIT = 200
MAX_FILE_PREVIEW_BYTES = 1_000_000


def dashboard_runs(db_path: Path | str | None) -> list[dict]:
    """Return the dashboard run list using the canonical projection."""

    with connect(db_path) as conn:
        _backfill_prompt_runs(conn)
        rows = all_rows(conn, "SELECT * FROM runs ORDER BY started_at DESC, id DESC")
    return [_run_list_dict(row) for row in rows]


def dashboard_sessions(db_path: Path | str | None) -> list[dict]:
    """Return stored sessions plus standalone runs for the chat-oriented view."""

    with connect(db_path) as conn:
        _backfill_prompt_runs(conn)
        rows = all_rows(
            conn,
            """
            SELECT
                s.id AS id,
                s.repository_path AS repository_path,
                s.started_at AS started_at,
                s.ended_at AS ended_at,
                s.final_outcome AS final_outcome,
                s.task_summary AS task_summary,
                1 AS is_session,
                (SELECT COUNT(*) FROM runs r WHERE r.session_id = s.id) AS turn_count,
                (SELECT model FROM runs r WHERE r.session_id = s.id ORDER BY turn_number DESC LIMIT 1) AS model,
                (SELECT agent_adapter FROM runs r WHERE r.session_id = s.id ORDER BY turn_number DESC LIMIT 1) AS agent_adapter,
                (SELECT agent_status FROM runs r WHERE r.session_id = s.id ORDER BY turn_number DESC LIMIT 1) AS agent_status,
                (SELECT verifier_status FROM runs r WHERE r.session_id = s.id ORDER BY turn_number DESC LIMIT 1) AS verifier_status,
                (SELECT human_status FROM runs r WHERE r.session_id = s.id ORDER BY turn_number DESC LIMIT 1) AS human_status
            FROM sessions s

            UNION ALL

            SELECT
                r.id AS id,
                r.repository_path AS repository_path,
                r.started_at AS started_at,
                r.completed_at AS ended_at,
                r.verifier_status AS final_outcome,
                substr(COALESCE(r.prompt, ''), 1, 240) AS task_summary,
                0 AS is_session,
                1 AS turn_count,
                r.model AS model,
                r.agent_adapter AS agent_adapter,
                r.agent_status AS agent_status,
                r.verifier_status AS verifier_status,
                r.human_status AS human_status
            FROM runs r
            WHERE r.session_id IS NULL OR r.session_id = ''

            ORDER BY started_at DESC, id DESC
            """,
        )
    return [_row_to_dict(row) for row in rows]


def dashboard_run_details(
    db_path: Path | str | None,
    run_id: str,
) -> dict[str, object]:
    """Return one run using the same projection served by every dashboard."""

    with connect(db_path) as conn:
        _backfill_prompt_runs(conn)
        run = one(conn, "SELECT * FROM runs WHERE id=?", [run_id])
        if not run:
            raise KeyError(run_id)
        return _dashboard_turn_details(conn, run)


def dashboard_session_details(
    db_path: Path | str | None,
    session_id: str,
) -> dict[str, object]:
    """Return all turns for a session, or one standalone run as a pseudo-session."""

    with connect(db_path) as conn:
        _backfill_prompt_runs(conn)
        session_row = one(conn, "SELECT * FROM sessions WHERE id=?", [session_id])
        if session_row:
            session = _row_to_dict(session_row)
            runs = all_rows(
                conn,
                "SELECT * FROM runs WHERE session_id=? ORDER BY turn_number ASC, started_at ASC",
                [session_id],
            )
        else:
            run_row = one(conn, "SELECT * FROM runs WHERE id=?", [session_id])
            if not run_row:
                raise KeyError(session_id)
            run = _row_to_dict(run_row)
            session = {
                "id": session_id,
                "repository_path": run["repository_path"],
                "repository_remote_hash": None,
                "started_at": run["started_at"],
                "ended_at": run["completed_at"],
                "final_outcome": run["verifier_status"],
                "task_summary": run["prompt"][:240] if run["prompt"] else "",
            }
            runs = [run_row]

        return {
            "session": session,
            "turns": [_dashboard_turn_details(conn, run) for run in runs],
        }


def is_known_file_path(conn: sqlite3.Connection, file_path: Path) -> bool:
    """Return whether a path has an explicit artifact or verifier-log record."""

    try:
        _resolve_dashboard_file(conn, requested_path=file_path)
    except PermissionError:
        return False
    return True


def read_dashboard_file(
    db_path: Path | str | None,
    requested_path: Path | str | None = None,
    *,
    reference_id: str | None = None,
) -> dict[str, object]:
    """Read a bounded preview of an explicitly persisted artifact or log."""

    with connect(db_path) as conn:
        file_path, matched_reference = _resolve_dashboard_file(
            conn,
            requested_path=requested_path,
            reference_id=reference_id,
        )
    raw = file_path.read_bytes()
    return {
        "path": str(file_path),
        "reference_id": matched_reference,
        "content": raw[:MAX_FILE_PREVIEW_BYTES].decode("utf-8", errors="replace"),
        "truncated": len(raw) > MAX_FILE_PREVIEW_BYTES,
    }


def _resolve_dashboard_file(
    conn: sqlite3.Connection,
    *,
    requested_path: Path | str | None = None,
    reference_id: str | None = None,
) -> tuple[Path, str]:
    requested = (
        _normalized_path(Path(requested_path).expanduser())
        if requested_path is not None
        else None
    )
    if requested is None and not reference_id:
        raise PermissionError("an artifact or verifier-log reference is required")

    for candidate_reference, candidate_path in _dashboard_file_references(conn):
        normalized = _normalized_path(candidate_path)
        if reference_id and candidate_reference != reference_id:
            continue
        if requested is not None and normalized != requested:
            continue
        return Path(normalized), candidate_reference

    raise PermissionError(
        "path is not a persisted Agent Quality artifact or verifier log"
    )


def _dashboard_file_references(
    conn: sqlite3.Connection,
) -> list[tuple[str, Path]]:
    references: list[tuple[str, Path]] = []
    for row in all_rows(conn, "SELECT id, path FROM artifacts"):
        if row["path"]:
            references.append(
                (f"artifact:{row['id']}", Path(row["path"]).expanduser())
            )
    for row in all_rows(
        conn,
        "SELECT id, stdout_path, stderr_path FROM verifier_results",
    ):
        for stream in ("stdout", "stderr"):
            candidate = row[f"{stream}_path"]
            if candidate:
                references.append(
                    (
                        f"verifier:{row['id']}:{stream}",
                        Path(candidate).expanduser(),
                    )
                )
    return references


def _dashboard_turn_details(
    conn: sqlite3.Connection,
    run: sqlite3.Row,
) -> dict[str, object]:
    run_id = run["id"]
    return {
        "run": _run_to_dict(run),
        "artifacts": [
            _row_to_dict(row)
            for row in all_rows(
                conn,
                "SELECT * FROM artifacts WHERE run_id=? ORDER BY artifact_type, path",
                [run_id],
            )
        ]
        + _event_artifacts(conn, run_id),
        "verifier_results": [
            _row_to_dict(row)
            for row in all_rows(
                conn,
                "SELECT * FROM verifier_results WHERE run_id=? ORDER BY started_at, verifier_name",
                [run_id],
            )
        ],
        "events": [
            _event_to_dict(row)
            for row in all_rows(
                conn,
                """
                SELECT *
                FROM events
                WHERE run_id=?
                ORDER BY COALESCE(sequence_number, rowid), rowid
                """,
                [run_id],
            )
        ],
        "agent_outputs": _agent_outputs(conn, run_id),
        "reasoning_trace": _reasoning_trace(conn, run_id),
        "tool_calls": _tool_calls(conn, run_id),
        "human_reviews": [
            _row_to_dict(row)
            for row in all_rows(
                conn,
                "SELECT * FROM human_reviews WHERE run_id=? ORDER BY reviewed_at DESC, rowid DESC",
                [run_id],
            )
        ],
    }


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def _compact_text(value: object, limit: int = DASHBOARD_TEXT_LIMIT) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n\n[truncated: {omitted} chars omitted]"


def _compact_value(value: object, text_limit: int = DASHBOARD_TEXT_LIMIT) -> object:
    if isinstance(value, str):
        return _compact_text(value, text_limit)
    if isinstance(value, list):
        items = [_compact_value(item, text_limit) for item in value[:DASHBOARD_LIST_LIMIT]]
        if len(value) > DASHBOARD_LIST_LIMIT:
            items.append({"_truncated_items": len(value) - DASHBOARD_LIST_LIMIT})
        return items
    if isinstance(value, dict):
        return {str(key): _compact_value(item, text_limit) for key, item in value.items()}
    return value


def _run_to_dict(row: sqlite3.Row) -> dict:
    data = _row_to_dict(row)
    if data.get("prompt"):
        data["prompt"] = _compact_text(data["prompt"], DASHBOARD_OUTPUT_LIMIT)
    return data


def _run_list_dict(row: sqlite3.Row) -> dict:
    data = _row_to_dict(row)
    if data.get("prompt"):
        data["prompt"] = _compact_text(data["prompt"], 240)
    return data


def _event_to_dict(row: sqlite3.Row) -> dict:
    data = _row_to_dict(row)
    for key in (
        "normalized_payload",
        "source_payload_sanitized",
        "provider_extensions",
        "redaction_findings",
    ):
        raw = data.get(key)
        if raw:
            data[f"{key}_json"] = _compact_value(_json_or_value(raw))
            data[key] = _compact_text(raw, 2000)
    return data


def _json_or_value(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _backfill_prompt_runs(conn: sqlite3.Connection) -> None:
    rows = all_rows(
        conn,
        """
        SELECT *
        FROM events
        WHERE source_event_type='UserPromptSubmit'
          AND (run_id IS NOT NULL OR source_payload_sanitized IS NOT NULL)
        ORDER BY COALESCE(occurred_at, observed_at), rowid
        """,
    )
    for row in rows:
        existing_run_id = row["run_id"]
        if existing_run_id and one(conn, "SELECT id FROM runs WHERE id=?", [existing_run_id]):
            continue
        payload = _json_or_value(row["source_payload_sanitized"])
        if not isinstance(payload, dict):
            continue
        context = hook_context_from_source(payload)
        if context is None:
            continue
        adapter, hook = context
        prompt = str(hook.get("prompt") or "").strip()
        if not prompt:
            continue
        run_id = existing_run_id or f"run_{_sha256_text(row['id'])[:32]}"
        if one(conn, "SELECT id FROM runs WHERE id=?", [run_id]):
            continue
        session_id = row["session_id"] or hook.get("session_id")
        started_at = row["occurred_at"] or row["observed_at"]
        repo_path = str(hook.get("cwd") or "")
        if session_id:
            conn.execute(
                """
                INSERT OR IGNORE INTO sessions (
                    id, repository_path, repository_remote_hash, started_at, ended_at, final_outcome, task_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, repo_path or "unknown", None, started_at, None, None, prompt[:240]),
            )
            turn_number = (
                conn.execute(
                    "SELECT COALESCE(MAX(turn_number), 0) + 1 AS n FROM runs WHERE session_id=?",
                    [session_id],
                ).fetchone()["n"]
            )
        else:
            turn_number = 1
        insert(
            conn,
            "runs",
            {
                "id": run_id,
                "session_id": session_id,
                "turn_number": turn_number,
                "prompt": prompt,
                "prompt_hash": _sha256_text(prompt),
                "repository_path": repo_path or "unknown",
                "base_commit": "unknown",
                "resulting_commit": None,
                "model": hook.get("model"),
                "agent_adapter": adapter.hook_agent_adapter,
                "agent_version": None,
                "wrapper_version": None,
                "codex_config_hash": None,
                "agents_md_hash": None,
                "verifier_version": None,
                "started_at": started_at,
                "completed_at": None,
                "duration_ms": None,
                "agent_status": "prompt_submitted",
                "verifier_status": "unverified",
                "human_status": "not_reviewed",
                "lifecycle_status": "still_open",
                "input_tokens": None,
                "cached_input_tokens": None,
                "output_tokens": None,
            },
            or_action="OR IGNORE",
        )
    _backfill_session_event_run_ids(conn)


def _backfill_session_event_run_ids(conn: sqlite3.Connection) -> None:
    rows = all_rows(
        conn,
        """
        SELECT rowid, run_id, session_id
        FROM events
        WHERE source_event_type='UserPromptSubmit'
          AND session_id IS NOT NULL
          AND run_id IS NOT NULL
        ORDER BY session_id, rowid
        """,
    )
    for row in rows:
        next_row = one(
            conn,
            """
            SELECT MIN(rowid) AS rowid
            FROM events
            WHERE session_id=?
              AND source_event_type='UserPromptSubmit'
              AND rowid>?
            """,
            [row["session_id"], row["rowid"]],
        )
        next_rowid = next_row["rowid"] if next_row else None
        if next_rowid is None:
            conn.execute(
                """
                UPDATE events
                SET run_id=?
                WHERE session_id=?
                  AND run_id IS NULL
                  AND rowid>=?
                """,
                [row["run_id"], row["session_id"], row["rowid"]],
            )
        else:
            conn.execute(
                """
                UPDATE events
                SET run_id=?
                WHERE session_id=?
                  AND run_id IS NULL
                  AND rowid>=?
                  AND rowid<?
                """,
                [row["run_id"], row["session_id"], row["rowid"], next_rowid],
            )


def _sha256_text(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _agent_outputs(conn: sqlite3.Connection, run_id: str) -> list[dict[str, object]]:
    outputs: list[dict[str, object]] = []
    rows = all_rows(
        conn,
        """
        SELECT *
        FROM events
        WHERE run_id=?
        ORDER BY COALESCE(sequence_number, rowid), rowid
        """,
        [run_id],
    )
    for row in rows:
        payload = _json_or_value(row["normalized_payload"])
        context = _event_hook_context(row)
        hook = context[1] if context else None
        text = None
        file_links = []
        if isinstance(payload, dict):
            text = payload.get("assistant_output")
            file_links = payload.get("file_links") if isinstance(payload.get("file_links"), list) else []
        if not text and context:
            adapter, hook = context
            text = adapter.assistant_output(row["source_event_type"], hook)
            file_links = adapter.file_links(hook, str(text) if text else None)
        if not text:
            continue
        outputs.append(
            {
                "event_id": row["id"],
                "sequence_number": row["sequence_number"],
                "occurred_at": row["occurred_at"] or row["observed_at"],
                "text": _compact_text(text, DASHBOARD_OUTPUT_LIMIT),
                "file_links": file_links,
            }
        )
    return outputs


def _reasoning_trace(conn: sqlite3.Connection, run_id: str) -> list[dict[str, object]]:
    trace: list[dict[str, object]] = []
    for row in all_rows(
        conn,
        "SELECT * FROM events WHERE run_id=? ORDER BY COALESCE(occurred_at, observed_at), rowid",
        [run_id],
    ):
        payload = _json_or_value(row["normalized_payload"])
        if not isinstance(payload, dict) or not payload.get("reasoning"):
            continue
        trace.append(
            {
                "event_id": row["id"],
                "sequence_number": row["sequence_number"],
                "occurred_at": row["occurred_at"] or row["observed_at"],
                "kind": payload.get("reasoning_kind") or "summary",
                "text": _compact_text(payload["reasoning"]),
            }
        )
    return trace


def _tool_calls(conn: sqlite3.Connection, run_id: str) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []
    by_id: dict[str, dict[str, object]] = {}
    rows = all_rows(
        conn,
        "SELECT * FROM events WHERE run_id=? ORDER BY COALESCE(sequence_number, rowid), rowid",
        [run_id],
    )
    for row in rows:
        payload = _json_or_value(row["normalized_payload"])
        payload = payload if isinstance(payload, dict) else {}
        hook = _hook_payload(row) or {}
        source_type = str(row["source_event_type"] or "")
        is_started = source_type == "PreToolUse" or row["event_type"] == "agent.tool.started"
        is_completed = source_type == "PostToolUse" or row["event_type"] == "agent.tool.completed"
        if not is_started and not is_completed:
            continue
        call_id = payload.get("tool_call_id") or hook.get("tool_use_id") or hook.get("call_id")
        tool_name = payload.get("tool_name") or hook.get("tool_name") or hook.get("toolName")
        tool_category = row["tool_category"] or payload.get("tool_category")
        if isinstance(tool_name, str) and tool_name.lower().startswith("mcp__"):
            tool_category = "mcp"
        key = str(call_id) if call_id else f"{tool_name}:{row['id']}"
        call = by_id.get(key)
        if call is None:
            call = {
                "event_id": row["id"],
                "call_id": call_id,
                "sequence_number": row["sequence_number"],
                "occurred_at": row["occurred_at"] or row["observed_at"],
                "tool_name": tool_name or row["tool_category"] or "tool",
                "tool_category": tool_category,
                "status": row["status"],
                "input": _compact_value(
                    payload.get("tool_input", hook.get("tool_input", hook.get("toolInput")))
                ),
                "output": None,
            }
            by_id[key] = call
            calls.append(call)
        elif call.get("input") is None:
            call["input"] = _compact_value(
                payload.get("tool_input", hook.get("tool_input", hook.get("toolInput")))
            )
        if is_completed:
            call["status"] = row["status"] or "completed"
            call["output"] = _compact_value(
                payload.get(
                    "tool_output",
                    hook.get("tool_response", hook.get("toolResponse")),
                )
            )
    return calls


def _event_artifacts(conn: sqlite3.Connection, run_id: str) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in all_rows(conn, "SELECT * FROM events WHERE run_id=? ORDER BY rowid", [run_id]):
        for item in _event_artifact_items(row):
            path = item.get("path")
            if not isinstance(path, str) or not path or path in seen:
                continue
            seen.add(path)
            file_path = Path(path).expanduser()
            size = file_path.stat().st_size if file_path.exists() and file_path.is_file() else None
            artifacts.append(
                {
                    "id": f"event_artifact_{_sha256_text(path)[:16]}",
                    "run_id": run_id,
                    "artifact_type": item.get("artifact_type") or "linked_file",
                    "path": path,
                    "line": item.get("line"),
                    "sha256": None,
                    "size_bytes": size,
                }
            )
    return artifacts


def _event_file_paths(conn: sqlite3.Connection) -> list[str]:
    paths: list[str] = []
    for row in all_rows(conn, "SELECT * FROM events"):
        for item in _event_artifact_items(row):
            path = item.get("path")
            if isinstance(path, str) and path:
                paths.append(path)
    return paths


def _event_artifact_items(row: sqlite3.Row) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    payload = _json_or_value(row["normalized_payload"])
    if isinstance(payload, dict):
        if isinstance(payload.get("path"), str) and payload["path"]:
            items.append({"artifact_type": "event_path", "path": payload["path"]})
        for link in payload.get("file_links") or []:
            if isinstance(link, dict) and isinstance(link.get("path"), str):
                items.append({"artifact_type": "linked_file", **link})
        for artifact in payload.get("artifacts") or []:
            if isinstance(artifact, dict) and isinstance(artifact.get("path"), str):
                items.append(artifact)
    context = _event_hook_context(row)
    if context:
        adapter, hook = context
        for artifact in adapter.artifacts(hook):
            items.append(artifact)
        output = adapter.assistant_output(row["source_event_type"], hook)
        for link in adapter.file_links(hook, output):
            items.append({"artifact_type": "linked_file", **link})
    return items


def _hook_payload(row: sqlite3.Row) -> dict[str, object] | None:
    context = _event_hook_context(row)
    return context[1] if context else None


def _event_hook_context(
    row: sqlite3.Row,
) -> tuple[HookProviderAdapter, dict[str, object]] | None:
    extensions = _json_or_value(row["provider_extensions"])
    if isinstance(extensions, dict):
        context = hook_context(extensions)
        if context is not None:
            return context

    source_payload = _json_or_value(row["source_payload_sanitized"])
    if isinstance(source_payload, dict):
        context = hook_context_from_source(source_payload)
        if context is not None:
            return context
    return None


def _normalized_path(path: Path) -> str:
    return str(path.resolve(strict=False))
