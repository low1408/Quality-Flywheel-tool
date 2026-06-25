from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent_quality.collector.envelope import normalize_envelope
from agent_quality.db import connect, insert


class CollectorHandler(BaseHTTPRequestHandler):
    db_path: Path | None = None
    bearer_token: str | None = None

    def do_POST(self) -> None:
        if self.path != "/v1/events":
            self.send_error(404)
            return
        if self.bearer_token:
            expected = f"Bearer {self.bearer_token}"
            if self.headers.get("Authorization") != expected:
                self.send_error(401)
                return
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            self.send_error(413)
            return
        payload = self.rfile.read(length)
        try:
            envelope = json.loads(payload)
            row = normalize_envelope(envelope)
            with connect(self.db_path) as conn:
                try:
                    insert(conn, "events", row)
                except Exception as exc:
                    if "UNIQUE constraint failed" not in str(exc):
                        raise
            self.send_response(202)
            self.end_headers()
            self.wfile.write(json.dumps({"event_id": row["id"]}).encode("utf-8"))
        except Exception as exc:
            self.send_error(400, str(exc))

    def log_message(self, format: str, *args: object) -> None:
        return


def serve(host: str = "127.0.0.1", port: int = 8765, db_path: Path | None = None, token: str | None = None) -> None:
    CollectorHandler.db_path = db_path
    CollectorHandler.bearer_token = token
    server = ThreadingHTTPServer((host, port), CollectorHandler)
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
