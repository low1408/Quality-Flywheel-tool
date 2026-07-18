from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from agent_quality.collector.dashboard import (
    dashboard_run_details,
    dashboard_runs,
    dashboard_session_details,
    dashboard_sessions,
    read_dashboard_file,
)
from agent_quality.collector.envelope import normalize_envelope
from agent_quality.db import connect, insert
from agent_quality.review.service import save_review_api


MAX_CONTENT_LENGTH = 1_000_000

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
        if parsed.path.startswith("/v1/ui/api/") and not self._authorize_api():
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
        if parsed.path not in {"/v1/events", "/v1/ui/api/review"}:
            self.send_error(404)
            return
        if not self._authorize_api():
            return
        if parsed.path == "/v1/ui/api/review":
            self._handle_ui_review()
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
            self._send_json({"event_id": row["id"]}, status=202)
        except Exception as exc:
            print(f"collector rejected event: {exc}", file=sys.stderr)
            self._send_json_error(400, "invalid event payload")

    def log_message(self, format: str, *args: object) -> None:
        return

    def _authorize_api(self) -> bool:
        token = self.server.bearer_token
        if not token:
            return True
        expected = f"Bearer {token}"
        supplied = self.headers.get("Authorization", "")
        if hmac.compare_digest(supplied, expected):
            return True
        self._send_json_error(
            401,
            "authentication required",
            extra_headers={"WWW-Authenticate": 'Bearer realm="agent-quality"'},
        )
        return False

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

    def _send_json_error(
        self,
        status: int,
        message: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._send_json(
            {"error": message},
            status=status,
            extra_headers=extra_headers,
        )

    def _handle_ui_runs(self) -> None:
        self._send_json(dashboard_runs(self.server.db_path))

    def _handle_ui_sessions(self) -> None:
        self._send_json(dashboard_sessions(self.server.db_path))

    def _handle_ui_run(self, run_id: str) -> None:
        try:
            payload = dashboard_run_details(self.server.db_path, run_id)
        except KeyError:
            self._send_json_error(404, "unknown run")
            return
        self._send_json(payload)

    def _handle_ui_session_details(self, session_id: str) -> None:
        try:
            payload = dashboard_session_details(self.server.db_path, session_id)
        except KeyError:
            self._send_json_error(404, "unknown session or run")
            return
        self._send_json(payload)

    def _handle_ui_log(self, requested_path: str | None) -> None:
        if not requested_path:
            self._send_json_error(400, "missing path")
            return
        try:
            payload = read_dashboard_file(self.server.db_path, requested_path)
        except PermissionError as exc:
            self._send_json_error(403, str(exc))
            return
        except OSError as exc:
            self._send_json_error(404, f"unable to read file: {exc.strerror or exc}")
            return
        self._send_json(payload)

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
        self._send_security_headers(document=filename == "dashboard.html")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(
        self,
        payload: object,
        status: int = 200,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self._send_security_headers()
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _send_security_headers(self, *, document: bool = False) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        if document:
            self.send_header(
                "Content-Security-Policy",
                "; ".join(
                    (
                        "default-src 'self'",
                        "img-src 'self' data:",
                        "style-src 'self'",
                        "script-src 'self'",
                        "connect-src 'self'",
                        "base-uri 'none'",
                        "form-action 'none'",
                        "frame-ancestors 'none'",
                    )
                ),
            )


def _is_unique_constraint(exc: sqlite3.IntegrityError) -> bool:
    if getattr(exc, "sqlite_errorname", "") in {
        "SQLITE_CONSTRAINT_UNIQUE",
        "SQLITE_CONSTRAINT_PRIMARYKEY",
    }:
        return True
    return "UNIQUE constraint failed" in str(exc)


def _empty_to_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float_or_none(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _int_or_none(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    db_path: Path | None = None,
    token: str | None = None,
) -> None:
    if not token:
        print(
            "warning: collector is running without bearer-token authentication",
            file=sys.stderr,
        )
    server = CollectorServer(
        (host, port),
        CollectorHandler,
        db_path=db_path,
        bearer_token=token,
    )
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
