import json

from agent_quality.adapters.codex_hooks import ingest_hook_event
from agent_quality.db import all_rows, connect, one


def test_ingests_codex_hook_event(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path))
    event_id = ingest_hook_event(
        "PostToolUse",
        {"threadId": "thr_1", "toolName": "Bash", "command": "pytest -q", "exitCode": 0},
    )
    rows = all_rows(connect(), "SELECT * FROM events WHERE id=?", [event_id])
    assert rows[0]["source_event_type"] == "PostToolUse"
    assert rows[0]["tool_category"] == "test"
    assert rows[0]["command"] == "pytest -q"


def test_ingests_mcp_tool_name_and_input(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path))
    event_id = ingest_hook_event(
        "PreToolUse",
        {
            "session_id": "ses_mcp",
            "tool_name": "mcp__quorum__consult_council",
            "tool_use_id": "call_mcp_1",
            "tool_input": {"question": "Review this change"},
        },
    )

    event = one(connect(), "SELECT * FROM events WHERE id=?", [event_id])
    payload = json.loads(event["normalized_payload"])
    assert event["tool_category"] == "mcp"
    assert payload["tool_call_id"] == "call_mcp_1"
    assert payload["tool_input"] == {"question": "Review this change"}


def test_user_prompt_submit_creates_visible_run(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path))
    event_id = ingest_hook_event(
        "UserPromptSubmit",
        {
            "event_id": "evt_prompt_1",
            "threadId": "thr_1",
            "prompt": "explain the prompt dashboard",
            "model": "test-model",
            "timestamp": "2026-01-01T00:00:00.000Z",
        },
    )

    conn = connect()
    event = one(conn, "SELECT run_id, item_type FROM events WHERE id=?", [event_id])
    run = one(conn, "SELECT * FROM runs WHERE id=?", [event["run_id"]])
    session = one(conn, "SELECT * FROM sessions WHERE id=?", ["thr_1"])

    assert event["item_type"] == "user_prompt"
    assert run["prompt"] == "explain the prompt dashboard"
    assert run["model"] == "test-model"
    assert run["agent_adapter"] == "codex-hooks"
    assert run["agent_status"] == "prompt_submitted"
    assert run["verifier_status"] == "unverified"
    assert session["task_summary"] == "explain the prompt dashboard"


def test_stop_hook_records_assistant_output_and_file_links(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path))
    linked = tmp_path / "app.py"
    linked.write_text("print('ok')\n", encoding="utf-8")
    ingest_hook_event(
        "UserPromptSubmit",
        {
            "event_id": "evt_prompt_output",
            "session_id": "ses_output",
            "prompt": "make a change",
            "timestamp": "2026-01-01T00:00:00.000Z",
        },
    )

    event_id = ingest_hook_event(
        "Stop",
        {
            "session_id": "ses_output",
            "last_assistant_message": f"Changed [app.py]({linked}:1).",
            "transcript_path": str(tmp_path / "transcript.jsonl"),
        },
    )

    conn = connect()
    event = one(conn, "SELECT * FROM events WHERE id=?", [event_id])
    payload = json.loads(event["normalized_payload"])
    run = one(conn, "SELECT * FROM runs WHERE id=?", [event["run_id"]])

    assert event["item_type"] == "assistant_output"
    assert payload["assistant_output"] == f"Changed [app.py]({linked}:1)."
    assert payload["file_links"][0]["path"] == str(linked)
    assert payload["file_links"][0]["line"] == 1
    assert payload["artifacts"][0]["artifact_type"] == "transcript"
    assert run["agent_status"] == "completed"


def test_stop_hook_imports_only_emitted_reasoning_from_current_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path))
    prompt = "trace this turn"
    transcript = tmp_path / "transcript.jsonl"
    records = [
        {"type": "event_msg", "payload": {"type": "user_message", "message": prompt}},
        {
            "timestamp": "2026-01-01T00:00:01.000Z",
            "type": "response_item",
            "payload": {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "Inspect the adapter first."}],
                "encrypted_content": "not-imported",
            },
        },
        {
            "timestamp": "2026-01-01T00:00:02.000Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "I found the capture boundary."}],
            },
        },
        {"type": "event_msg", "payload": {"type": "user_message", "message": "later turn"}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "Do not import this."}],
            },
        },
    ]
    transcript.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    ingest_hook_event(
        "UserPromptSubmit",
        {"event_id": "evt_trace_prompt", "session_id": "ses_trace", "prompt": prompt},
    )

    ingest_hook_event(
        "Stop",
        {
            "session_id": "ses_trace",
            "last_assistant_message": "Done.",
            "transcript_path": str(transcript),
        },
    )

    rows = all_rows(connect(), "SELECT * FROM events WHERE event_type='agent.reasoning' ORDER BY occurred_at")
    trace = [json.loads(row["normalized_payload"]) for row in rows]
    assert [item["reasoning"] for item in trace] == [
        "Inspect the adapter first.",
        "I found the capture boundary.",
    ]
    assert all("encrypted_content" not in item for item in trace)
