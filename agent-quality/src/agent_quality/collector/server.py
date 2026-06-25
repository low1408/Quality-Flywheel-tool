from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

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
        if parsed.path.startswith("/v1/ui/api/run/"):
            self._handle_ui_run(unquote(parsed.path.rsplit("/", 1)[-1]))
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
            rows = all_rows(conn, "SELECT * FROM runs ORDER BY started_at DESC, id DESC")
        self._send_json([_row_to_dict(row) for row in rows])

    def _handle_ui_run(self, run_id: str) -> None:
        with connect(self.server.db_path) as conn:
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
                ],
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
    return False


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
