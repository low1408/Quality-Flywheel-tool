from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent_quality.collector.envelope import normalize_envelope
from agent_quality.db import connect, insert

MAX_CONTENT_LENGTH = 1_000_000


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
    def do_POST(self) -> None:
        if self.path != "/v1/events":
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


def _is_unique_constraint(exc: sqlite3.IntegrityError) -> bool:
    if getattr(exc, "sqlite_errorname", "") in {"SQLITE_CONSTRAINT_UNIQUE", "SQLITE_CONSTRAINT_PRIMARYKEY"}:
        return True
    return "UNIQUE constraint failed" in str(exc)


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
