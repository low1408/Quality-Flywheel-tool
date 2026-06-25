from agent_quality.adapters.codex_hooks import ingest_hook_event
from agent_quality.db import all_rows, connect


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
