from __future__ import annotations

import json
from pathlib import Path

from agent_quality.cli import _install_antigravity_hooks
from agent_quality.adapters.antigravity import ingest_hook_event, rows_from_jsonl, extract_usage
from agent_quality.db import all_rows, connect, one


def test_install_antigravity_hooks_targets_git_root(tmp_path):
    repo = tmp_path / "repo"
    nested = repo / "agent-quality"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()

    _install_antigravity_hooks(nested, "python3")

    root_hooks = repo / ".agents" / "hooks.json"
    nested_hooks = nested / ".agents" / "hooks.json"

    assert root_hooks.exists()
    assert not nested_hooks.exists()

    hooks = json.loads(root_hooks.read_text(encoding="utf-8"))
    assert "agent-quality" in hooks
    assert "PreToolUse" in hooks["agent-quality"]
    command = hooks["agent-quality"]["PreToolUse"][0]["hooks"][0]["command"]
    assert f"AGENT_QUALITY_HOME={repo / '.agent-quality' / 'local'}" in command


def test_install_antigravity_hooks_preserves_and_merges_existing_hooks(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    agents_dir = repo / ".agents"
    agents_dir.mkdir()
    hooks_file = agents_dir / "hooks.json"
    hooks_file.write_text(json.dumps({
        "existing-third-party": {
            "PreToolUse": [{"hooks": [{"type": "command", "command": "echo hello"}]}]
        }
    }), encoding="utf-8")

    _install_antigravity_hooks(repo, "python3")

    hooks = json.loads(hooks_file.read_text(encoding="utf-8"))
    assert "existing-third-party" in hooks
    assert "agent-quality" in hooks
    assert hooks["existing-third-party"]["PreToolUse"][0]["hooks"][0]["command"] == "echo hello"


def test_ingests_antigravity_hook_event(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path))
    event_id = ingest_hook_event(
        "PostToolUse",
        {"threadId": "thr_ag", "toolName": "Bash", "command": "pytest -q", "exitCode": 0},
    )
    rows = all_rows(connect(), "SELECT * FROM events WHERE id=?", [event_id])
    assert len(rows) == 1
    assert rows[0]["source_event_type"] == "PostToolUse"
    assert rows[0]["tool_category"] == "test"
    assert rows[0]["command"] == "pytest -q"


def test_antigravity_hook_redaction_and_privacy(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path))
    event_id = ingest_hook_event(
        "UserPromptSubmit",
        {
            "session_id": "ses_redact",
            "prompt": "fix sk-123456789012345678901234567890123456",  # OpenAI format api key mock
            "secret_key": "ghp_123456789012345678901234567890123456", # github token format mock
        },
    )
    conn = connect()
    event = one(conn, "SELECT * FROM events WHERE id=?", [event_id])
    run = one(conn, "SELECT * FROM runs WHERE id=?", [event["run_id"]])

    # Assert payload field is redacted
    assert "ghp_" not in event["source_payload_sanitized"]
    assert "sk-" not in run["prompt"]
    assert "[REDACTED" in event["source_payload_sanitized"]
    assert "sensitive_field" in json.loads(event["redaction_findings"])


def test_antigravity_hook_idempotency_deduplication(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path))
    payload = {"threadId": "thr_dedupe", "toolName": "Bash", "command": "echo ok", "exitCode": 0}
    
    # Ingest event first time
    id_1 = ingest_hook_event("PostToolUse", payload)
    
    # Ingest duplicate event second time
    id_2 = ingest_hook_event("PostToolUse", payload)
    
    # Verify that the DB only contains one event and it has id_1
    rows = all_rows(connect(), "SELECT * FROM events WHERE session_id=?", ["thr_dedupe"])
    assert len(rows) == 1
    assert rows[0]["id"] == id_1


def test_antigravity_rows_from_jsonl_single_report(tmp_path):
    lines = [
        '{',
        '  "exit_code": 0,',
        '  "output": "The task completed successfully.",',
        '  "usage": {',
        '    "input_tokens": 150,',
        '    "output_tokens": 45',
        '  }',
        '}'
    ]
    rows = rows_from_jsonl(lines, run_id="run_jsonl_1", session_id="ses_jsonl_1")
    assert len(rows) == 1
    assert rows[0]["source_event_type"] == "RunSummary"
    
    payload = json.loads(rows[0]["normalized_payload"])
    assert payload["assistant_output"] == "The task completed successfully."
    assert payload["status"] == "completed"

    input_tokens, _, output_tokens = extract_usage(lines)
    assert input_tokens == 150
    assert output_tokens == 45
