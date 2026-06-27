import http.client
import json
import threading

from agent_quality.adapters.codex_hooks import ingest_hook_event
from agent_quality.collector.envelope import make_envelope, normalize_envelope
from agent_quality.collector.server import CollectorHandler, CollectorServer
from agent_quality.db import all_rows, connect, insert, one


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


def test_collector_serves_ui_runs_details_and_review_api(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    db_path = tmp_path / "quality.sqlite3"
    conn = connect(db_path)
    with conn:
        _insert_run(conn, "run_ui", "not_reviewed", "2026-01-01T00:00:00.000Z")

    server = CollectorServer(("127.0.0.1", 0), CollectorHandler, db_path=db_path, bearer_token="secret")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn_http = _connection(server)
        conn_http.request("GET", "/v1/ui/")
        response = conn_http.getresponse()
        assert response.status == 200
        assert "Agent Quality" in response.read().decode("utf-8")

        conn_http = _connection(server)
        conn_http.request("GET", "/v1/ui/api/runs")
        response = conn_http.getresponse()
        runs = json.loads(response.read())
        assert response.status == 200
        assert runs[0]["id"] == "run_ui"

        conn_http = _connection(server)
        conn_http.request("GET", "/v1/ui/api/run/run_ui")
        response = conn_http.getresponse()
        details = json.loads(response.read())
        assert response.status == 200
        assert details["run"]["id"] == "run_ui"

        body = json.dumps(
            {
                "run_id": "run_ui",
                "outcome": "rejected",
                "primary_category": "implementation",
                "severity": "high",
                "notes": "needs work",
                "confidence": 0.8,
                "critical_sequence": 2,
            }
        ).encode("utf-8")
        conn_http = _connection(server)
        conn_http.request(
            "POST",
            "/v1/ui/api/review",
            body=body,
            headers={"Content-Length": str(len(body)), "Content-Type": "application/json"},
        )
        response = conn_http.getresponse()
        review = json.loads(response.read())
        assert response.status == 200
        assert review["outcome"] == "rejected"
        assert one(connect(db_path), "SELECT human_status FROM runs WHERE id=?", ["run_ui"])["human_status"] == "rejected"
    finally:
        server.shutdown()
        server.server_close()


def test_collector_log_endpoint_only_reads_known_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    db_path = tmp_path / "quality.sqlite3"
    log_path = tmp_path / "stdout.txt"
    log_path.write_text("verifier output\n", encoding="utf-8")
    conn = connect(db_path)
    with conn:
        _insert_run(conn, "run_log", "not_reviewed", "2026-01-01T00:00:00.000Z")
        insert(
            conn,
            "artifacts",
            {
                "id": "art_log",
                "run_id": "run_log",
                "artifact_type": "verifier_log",
                "path": str(log_path),
                "sha256": "sha",
                "size_bytes": log_path.stat().st_size,
            },
        )

    server = CollectorServer(("127.0.0.1", 0), CollectorHandler, db_path=db_path, bearer_token=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn_http = _connection(server)
        conn_http.request("GET", f"/v1/ui/api/log?path={log_path}")
        response = conn_http.getresponse()
        body = json.loads(response.read())
        assert response.status == 200
        assert body["content"] == "verifier output\n"

        conn_http = _connection(server)
        conn_http.request("GET", f"/v1/ui/api/log?path={tmp_path / 'other.txt'}")
        response = conn_http.getresponse()
        assert response.status == 403
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_details_backfills_hook_output_and_linked_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    db_path = tmp_path / "quality.sqlite3"
    linked = tmp_path / "linked.py"
    linked.write_text("print('linked')\n", encoding="utf-8")
    prompt_event_id = ingest_hook_event(
        "UserPromptSubmit",
        {
            "event_id": "evt_dashboard_prompt",
            "session_id": "ses_dashboard",
            "prompt": "inspect output",
            "timestamp": "2026-01-01T00:00:00.000Z",
        },
        db_path=db_path,
    )
    run_id = one(connect(db_path), "SELECT run_id FROM events WHERE id=?", [prompt_event_id])["run_id"]
    stop = normalize_envelope(
        make_envelope(
            event_type="agent.hook.stop",
            source_event_type="Stop",
            session_id="ses_dashboard",
            run_id=None,
            data={"status": "observed", "item_type": "lifecycle", "hook_event": "Stop"},
            extensions={
                "openai.codex.hook": {
                    "session_id": "ses_dashboard",
                    "last_assistant_message": f"Updated [linked.py]({linked}:1).",
                    "transcript_path": str(tmp_path / "transcript.jsonl"),
                }
            },
        )
    )
    with connect(db_path) as conn:
        insert(conn, "events", stop)

    server = CollectorServer(("127.0.0.1", 0), CollectorHandler, db_path=db_path, bearer_token=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn_http = _connection(server)
        conn_http.request("GET", f"/v1/ui/api/run/{run_id}")
        response = conn_http.getresponse()
        details = json.loads(response.read())

        assert response.status == 200
        assert details["agent_outputs"][0]["text"] == f"Updated [linked.py]({linked}:1)."
        assert any(artifact["path"] == str(linked) for artifact in details["artifacts"])
        assert one(connect(db_path), "SELECT run_id FROM events WHERE id=?", [stop["id"]])["run_id"] == run_id
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_details_separates_reasoning_and_tool_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    db_path = tmp_path / "quality.sqlite3"
    with connect(db_path) as conn:
        _insert_run(conn, "run_trace", "not_reviewed", "2026-01-01T00:00:00.000Z")
        reasoning = make_envelope(
            event_type="agent.reasoning",
            source_event_type="transcript.commentary",
            run_id="run_trace",
            data={
                "status": "completed",
                "item_type": "reasoning",
                "reasoning": "Inspect the event adapter.",
                "reasoning_kind": "commentary",
            },
        )
        reasoning["occurred_at"] = "2026-01-01T00:00:01.000Z"
        insert(conn, "events", normalize_envelope(reasoning))
        insert(
            conn,
            "events",
            normalize_envelope(
                make_envelope(
                    event_type="agent.tool.started",
                    source_event_type="PreToolUse",
                    run_id="run_trace",
                    data={
                        "status": "started",
                        "item_type": "mcp_tool_call",
                        "tool_category": "mcp",
                        "tool_name": "mcp__quorum__consult_council",
                        "tool_call_id": "call_1",
                        "tool_input": {"question": "Review this"},
                    },
                )
            ),
        )
        insert(
            conn,
            "events",
            normalize_envelope(
                make_envelope(
                    event_type="agent.tool.completed",
                    source_event_type="PostToolUse",
                    run_id="run_trace",
                    data={
                        "status": "success",
                        "item_type": "mcp_tool_call",
                        "tool_category": "mcp",
                        "tool_name": "mcp__quorum__consult_council",
                        "tool_call_id": "call_1",
                        "tool_output": "No defects.",
                    },
                )
            ),
        )

    server = CollectorServer(("127.0.0.1", 0), CollectorHandler, db_path=db_path, bearer_token=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn_http = _connection(server)
        conn_http.request("GET", "/v1/ui/api/run/run_trace")
        response = conn_http.getresponse()
        details = json.loads(response.read())

        assert response.status == 200
        assert details["reasoning_trace"][0]["text"] == "Inspect the event adapter."
        assert details["tool_calls"][0]["tool_name"] == "mcp__quorum__consult_council"
        assert details["tool_calls"][0]["input"] == {"question": "Review this"}
        assert details["tool_calls"][0]["output"] == "No defects."
    finally:
        server.shutdown()
        server.server_close()


def _insert_run(conn, run_id: str, human_status: str, started_at: str) -> None:
    insert(
        conn,
        "runs",
        {
            "id": run_id,
            "session_id": None,
            "turn_number": 1,
            "prompt": "test prompt",
            "prompt_hash": "hash",
            "repository_path": "/repo",
            "base_commit": "abc123",
            "resulting_commit": None,
            "model": None,
            "agent_adapter": "codex-cli",
            "agent_version": None,
            "wrapper_version": "test",
            "codex_config_hash": None,
            "agents_md_hash": None,
            "verifier_version": "verifier",
            "started_at": started_at,
            "completed_at": started_at,
            "duration_ms": 1,
            "agent_status": "completed",
            "verifier_status": "not_configured",
            "human_status": human_status,
            "lifecycle_status": "still_open",
            "input_tokens": None,
            "cached_input_tokens": None,
            "output_tokens": None,
        },
    )
