import http.client
import json
import threading

from agent_quality.collector.server import CollectorHandler, CollectorServer
from agent_quality.db import all_rows, connect


def _server(tmp_path, *, token: str | None = "secret") -> CollectorServer:
    server = CollectorServer(
        ("127.0.0.1", 0),
        CollectorHandler,
        db_path=tmp_path / "quality.sqlite3",
        bearer_token=token,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _connection(server: CollectorServer) -> http.client.HTTPConnection:
    host, port = server.server_address
    return http.client.HTTPConnection(host, port, timeout=5)


def _event(event_id: str) -> bytes:
    return json.dumps(
        {
            "event_id": event_id,
            "event_type": "agent.event",
            "source": {"provider": "test", "source_event_type": "test.event"},
            "data": {},
        }
    ).encode("utf-8")


def test_collector_rejects_missing_content_length(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    server = _server(tmp_path)
    try:
        conn = _connection(server)
        conn.putrequest("POST", "/v1/events")
        conn.putheader("Authorization", "Bearer secret")
        conn.endheaders()

        response = conn.getresponse()

        assert response.status == 400
    finally:
        server.shutdown()
        server.server_close()


def test_collector_sanitizes_bad_payload_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    server = _server(tmp_path)
    try:
        conn = _connection(server)
        conn.request(
            "POST",
            "/v1/events",
            body=b'{"event_id":',
            headers={"Authorization": "Bearer secret", "Content-Length": "12"},
        )

        response = conn.getresponse()
        body = response.read().decode("utf-8")

        assert response.status == 400
        assert "invalid event payload" in body
        assert "Expecting value" not in body
    finally:
        server.shutdown()
        server.server_close()


def test_collector_accepts_duplicate_events_idempotently(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    db_path = tmp_path / "quality.sqlite3"
    server = CollectorServer(("127.0.0.1", 0), CollectorHandler, db_path=db_path, bearer_token="secret")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for _ in range(2):
            conn = _connection(server)
            body = _event("evt_duplicate")
            conn.request(
                "POST",
                "/v1/events",
                body=body,
                headers={"Authorization": "Bearer secret", "Content-Length": str(len(body))},
            )
            assert conn.getresponse().status == 202

        rows = all_rows(connect(db_path), "SELECT id FROM events")
        assert [row["id"] for row in rows] == ["evt_duplicate"]
    finally:
        server.shutdown()
        server.server_close()


def test_collector_server_keeps_config_on_instance(tmp_path):
    first = CollectorServer(("127.0.0.1", 0), CollectorHandler, db_path=tmp_path / "one.sqlite3", bearer_token="one")
    second = CollectorServer(("127.0.0.1", 0), CollectorHandler, db_path=tmp_path / "two.sqlite3", bearer_token="two")
    try:
        assert first.bearer_token == "one"
        assert second.bearer_token == "two"
    finally:
        first.server_close()
        second.server_close()
