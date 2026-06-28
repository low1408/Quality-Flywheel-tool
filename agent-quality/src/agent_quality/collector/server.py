from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from agent_quality.adapters.codex_hooks import _artifacts, _assistant_output, _file_links
from agent_quality.collector.envelope import normalize_envelope
from agent_quality.db import all_rows, connect, insert, one
from agent_quality.review.service import save_review_api

MAX_CONTENT_LENGTH = 1_000_000
MAX_FILE_PREVIEW_BYTES = 1_000_000

STATIC_ASSETS = {
    "/v1/ui/dashboard.css": "dashboard.css",
    "/v1/ui/dashboard.js": "dashboard.js",
}


class CollectorServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        db_path: Path | None,
        bearer_token: str | None,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.db_path = db_path
        self.bearer_token = bearer_token


class CollectorHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/v1/ui", "/v1/ui/", "/v1/ui/index.html"}:
            self._send_static("dashboard.html")
            return
        if parsed.path in STATIC_ASSETS:
            self._send_static(STATIC_ASSETS[parsed.path])
            return
        if parsed.path == "/v1/ui/api/runs":
            self._handle_ui_runs()
            return
        if parsed.path == "/v1/ui/api/sessions":
            self._handle_ui_sessions()
            return
        if parsed.path.startswith("/v1/ui/api/run/"):
            self._handle_ui_run(unquote(parsed.path.rsplit("/", 1)[-1]))
            return
        if parsed.path.startswith("/v1/ui/api/session/"):
            self._handle_ui_session_details(unquote(parsed.path.rsplit("/", 1)[-1]))
            return
        if parsed.path == "/v1/ui/api/log":
            query = parse_qs(parsed.query)
            self._handle_ui_log(query.get("path", [None])[0])
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/v1/ui/api/review":
            self._handle_ui_review()
            return
        if parsed.path != "/v1/events":
            self.send_error(404)
            return
        if self.server.bearer_token:
            expected = f"Bearer {self.server.bearer_token}"
            if self.headers.get("Authorization") != expected:
                self.send_error(401)
                return

        length = self._content_length()
        if length is None:
            return
        payload = self.rfile.read(length)
        if len(payload) != length:
            self._send_json_error(400, "incomplete request body")
            return
        try:
            envelope = json.loads(payload)
            row = normalize_envelope(envelope)
            with connect(self.server.db_path) as conn:
                try:
                    insert(conn, "events", row)
                except sqlite3.IntegrityError as exc:
                    if not _is_unique_constraint(exc):
                        raise
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"event_id": row["id"]}).encode("utf-8"))
        except Exception as exc:
            print(f"collector rejected event: {exc}", file=sys.stderr)
            self._send_json_error(400, "invalid event payload")

    def log_message(self, format: str, *args: object) -> None:
        return

    def _content_length(self) -> int | None:
        raw = self.headers.get("Content-Length")
        if raw is None:
            self._send_json_error(400, "missing Content-Length")
            return None
        try:
            length = int(raw)
        except ValueError:
            self._send_json_error(400, "invalid Content-Length")
            return None
        if length <= 0:
            self._send_json_error(400, "invalid Content-Length")
            return None
        if length > MAX_CONTENT_LENGTH:
            self._send_json_error(413, "payload too large")
            return None
        return length

    def _send_json_error(self, status: int, message: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode("utf-8"))

    def _handle_ui_runs(self) -> None:
        with connect(self.server.db_path) as conn:
            _backfill_prompt_runs(conn)
            rows = all_rows(conn, "SELECT * FROM runs ORDER BY started_at DESC, id DESC")
        self._send_json([_row_to_dict(row) for row in rows])

    def _handle_ui_sessions(self) -> None:
        with connect(self.server.db_path) as conn:
            _backfill_prompt_runs(conn)
            sql = """
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
                r.prompt AS task_summary,
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
            """
            rows = all_rows(conn, sql)
        self._send_json([_row_to_dict(row) for row in rows])

    def _handle_ui_run(self, run_id: str) -> None:
        with connect(self.server.db_path) as conn:
            _backfill_prompt_runs(conn)
            run = one(conn, "SELECT * FROM runs WHERE id=?", [run_id])
            if not run:
                self._send_json_error(404, "unknown run")
                return
            payload = {
                "run": _row_to_dict(run),
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
        self._send_json(payload)

    def _handle_ui_session_details(self, session_id: str) -> None:
        with connect(self.server.db_path) as conn:
            _backfill_prompt_runs(conn)
            session_row = one(conn, "SELECT * FROM sessions WHERE id=?", [session_id])
            if session_row:
                session_dict = _row_to_dict(session_row)
                runs = all_rows(conn, "SELECT * FROM runs WHERE session_id=? ORDER BY turn_number ASC, started_at ASC", [session_id])
            else:
                run_row = one(conn, "SELECT * FROM runs WHERE id=?", [session_id])
                if not run_row:
                    self._send_json_error(404, "unknown session or run")
                    return
                run_dict = _row_to_dict(run_row)
                session_dict = {
                    "id": session_id,
                    "repository_path": run_dict["repository_path"],
                    "repository_remote_hash": None,
                    "started_at": run_dict["started_at"],
                    "ended_at": run_dict["completed_at"],
                    "final_outcome": run_dict["verifier_status"],
                    "task_summary": run_dict["prompt"][:240] if run_dict["prompt"] else ""
                }
                runs = [run_row]
            
            turns_details = []
            all_artifacts = []
            all_verifier_results = []
            all_events = []
            
            for run in runs:
                r_id = run["id"]
                artifacts = [
                    _row_to_dict(row)
                    for row in all_rows(
                        conn,
                        "SELECT * FROM artifacts WHERE run_id=? ORDER BY artifact_type, path",
                        [r_id],
                    )
                ] + _event_artifacts(conn, r_id)
                
                verifier_results = [
                    _row_to_dict(row)
                    for row in all_rows(
                        conn,
                        "SELECT * FROM verifier_results WHERE run_id=? ORDER BY started_at, verifier_name",
                        [r_id],
                    )
                ]
                
                events = [
                    _event_to_dict(row)
                    for row in all_rows(
                        conn,
                        """
                        SELECT *
                        FROM events
                        WHERE run_id=?
                        ORDER BY COALESCE(sequence_number, rowid), rowid
                        """,
                        [r_id],
                    )
                ]
                
                outputs = _agent_outputs(conn, r_id)
                trace = _reasoning_trace(conn, r_id)
                calls = _tool_calls(conn, r_id)
                
                human_reviews = [
                    _row_to_dict(row)
                    for row in all_rows(
                        conn,
                        "SELECT * FROM human_reviews WHERE run_id=? ORDER BY reviewed_at DESC, rowid DESC",
                        [r_id],
                    )
                ]
                
                turns_details.append({
                    "run": _row_to_dict(run),
                    "artifacts": artifacts,
                    "verifier_results": verifier_results,
                    "events": events,
                    "agent_outputs": outputs,
                    "reasoning_trace": trace,
                    "tool_calls": calls,
                    "human_reviews": human_reviews
                })
                
                all_artifacts.extend(artifacts)
                all_verifier_results.extend(verifier_results)
                all_events.extend(events)
            
            payload = {
                "session": session_dict,
                "turns": turns_details,
                "all_artifacts": all_artifacts,
                "all_verifier_results": all_verifier_results,
                "all_events": all_events
            }
        self._send_json(payload)

    def _handle_ui_log(self, requested_path: str | None) -> None:
        if not requested_path:
            self._send_json_error(400, "missing path")
            return
        file_path = Path(requested_path).expanduser()
        with connect(self.server.db_path) as conn:
            if not _is_known_file_path(conn, file_path):
                self._send_json_error(403, "path is not a known Agent Quality artifact or verifier log")
                return
        try:
            raw = file_path.read_bytes()
        except OSError as exc:
            self._send_json_error(404, f"unable to read file: {exc.strerror or exc}")
            return
        truncated = len(raw) > MAX_FILE_PREVIEW_BYTES
        content = raw[:MAX_FILE_PREVIEW_BYTES].decode("utf-8", errors="replace")
        self._send_json({"path": str(file_path), "content": content, "truncated": truncated})

    def _handle_ui_review(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return
        run_id = payload.get("run_id")
        outcome = payload.get("outcome")
        if not run_id or not outcome:
            self._send_json_error(400, "run_id and outcome are required")
            return
        try:
            review = save_review_api(
                str(run_id),
                str(outcome),
                primary_category=_empty_to_none(payload.get("primary_category")),
                severity=_empty_to_none(payload.get("severity")),
                notes=str(payload.get("notes") or ""),
                confidence=_float_or_none(payload.get("confidence")),
                critical_sequence=_int_or_none(payload.get("critical_sequence")),
                db_path=self.server.db_path,
            )
        except ValueError as exc:
            self._send_json_error(404, str(exc))
            return
        except Exception as exc:
            print(f"collector rejected review: {exc}", file=sys.stderr)
            self._send_json_error(400, "invalid review payload")
            return
        self._send_json(review)

    def _read_json_body(self) -> dict | None:
        length = self._content_length()
        if length is None:
            return None
        payload = self.rfile.read(length)
        if len(payload) != length:
            self._send_json_error(400, "incomplete request body")
            return None
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            self._send_json_error(400, "invalid JSON request body")
            return None
        if not isinstance(data, dict):
            self._send_json_error(400, "JSON object expected")
            return None
        return data

    def _send_static(self, filename: str) -> None:
        path = Path(__file__).with_name("static") / filename
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type = f"{content_type}; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: object, status: int = 200) -> None:
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _is_unique_constraint(exc: sqlite3.IntegrityError) -> bool:
    if getattr(exc, "sqlite_errorname", "") in {"SQLITE_CONSTRAINT_UNIQUE", "SQLITE_CONSTRAINT_PRIMARYKEY"}:
        return True
    return "UNIQUE constraint failed" in str(exc)


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def _event_to_dict(row: sqlite3.Row) -> dict:
    data = _row_to_dict(row)
    for key in ("normalized_payload", "source_payload_sanitized", "provider_extensions", "redaction_findings"):
        if data.get(key):
            data[f"{key}_json"] = _json_or_value(data[key])
    return data


def _json_or_value(value: str) -> object:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
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
        hook = (((payload.get("extensions") or {}).get("openai.codex.hook")) or {})
        if not isinstance(hook, dict):
            continue
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
                conn.execute("SELECT COALESCE(MAX(turn_number), 0) + 1 AS n FROM runs WHERE session_id=?", [session_id])
                .fetchone()["n"]
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
                "agent_adapter": "codex-hooks",
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


def _is_known_file_path(conn: sqlite3.Connection, file_path: Path) -> bool:
    requested = _normalized_path(file_path)
    rows = all_rows(
        conn,
        """
        SELECT path FROM artifacts
        UNION
        SELECT stdout_path AS path FROM verifier_results WHERE stdout_path IS NOT NULL
        UNION
        SELECT stderr_path AS path FROM verifier_results WHERE stderr_path IS NOT NULL
        """,
    )
    for row in rows:
        candidate = row["path"]
        if candidate and _normalized_path(Path(candidate).expanduser()) == requested:
            return True
    for candidate in _event_file_paths(conn):
        if _normalized_path(Path(candidate).expanduser()) == requested:
            return True
    return False


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
        hook = _hook_payload(row)
        text = None
        file_links = []
        if isinstance(payload, dict):
            text = payload.get("assistant_output")
            file_links = payload.get("file_links") if isinstance(payload.get("file_links"), list) else []
        if not text and hook:
            text = _assistant_output(row["source_event_type"], hook)
            file_links = _file_links(hook, str(text) if text else None)
        if not text:
            continue
        outputs.append(
            {
                "event_id": row["id"],
                "sequence_number": row["sequence_number"],
                "occurred_at": row["occurred_at"] or row["observed_at"],
                "text": str(text),
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
                "occurred_at": row["occurred_at"] or row["observed_at"],
                "kind": payload.get("reasoning_kind") or "summary",
                "text": str(payload["reasoning"]),
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
                "occurred_at": row["occurred_at"] or row["observed_at"],
                "tool_name": tool_name or row["tool_category"] or "tool",
                "tool_category": tool_category,
                "status": row["status"],
                "input": payload.get("tool_input", hook.get("tool_input", hook.get("toolInput"))),
                "output": None,
            }
            by_id[key] = call
            calls.append(call)
        elif call.get("input") is None:
            call["input"] = payload.get("tool_input", hook.get("tool_input", hook.get("toolInput")))
        if is_completed:
            call["status"] = row["status"] or "completed"
            call["output"] = payload.get(
                "tool_output",
                hook.get("tool_response", hook.get("toolResponse")),
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
    hook = _hook_payload(row)
    if hook:
        for artifact in _artifacts(hook):
            items.append(artifact)
        output = _assistant_output(row["source_event_type"], hook)
        for link in _file_links(hook, output):
            items.append({"artifact_type": "linked_file", **link})
    return items


def _hook_payload(row: sqlite3.Row) -> dict[str, object] | None:
    extensions = _json_or_value(row["provider_extensions"])
    if not isinstance(extensions, dict):
        return None
    hook = extensions.get("openai.codex.hook")
    return hook if isinstance(hook, dict) else None


def _normalized_path(path: Path) -> str:
    return str(path.resolve(strict=False))


def _empty_to_none(value: object) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _float_or_none(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _int_or_none(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def serve(host: str = "127.0.0.1", port: int = 8765, db_path: Path | None = None, token: str | None = None) -> None:
    if not token:
        print("warning: collector is running without bearer-token authentication", file=sys.stderr)
    server = CollectorServer((host, port), CollectorHandler, db_path=db_path, bearer_token=token)
    print(f"collector listening on http://{host}:{port}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db")
    parser.add_argument("--token")
    args = parser.parse_args()
    serve(args.host, args.port, Path(args.db) if args.db else None, args.token)


if __name__ == "__main__":
    main()
