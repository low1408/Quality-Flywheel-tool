from __future__ import annotations

import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from agent_quality.adapters import antigravity
from agent_quality.adapters.antigravity import extract_usage, ingest_hook_event, rows_from_jsonl
from agent_quality.adapters.hook_runtime import initialize_project_consent
from agent_quality.db import all_rows, connect, one


CONVERSATION_ID = "ec33ebf9-0cba-4100-8142-c61503f6c587"


def _initialized_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    config = repo / ".agent-quality" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("version: 1\n", encoding="utf-8")
    consent_marker = initialize_project_consent(repo)
    assert consent_marker.is_file()
    return repo


def _common_payload(repo: Path) -> dict:
    return {
        "conversationId": CONVERSATION_ID,
        "workspacePaths": [str(repo)],
        "transcriptPath": str(repo / ".antigravity" / "transcript.jsonl"),
        "artifactDirectoryPath": str(repo / ".antigravity" / "artifacts"),
    }


def _invoke(monkeypatch, event_name: str, payload: dict) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return antigravity.main([event_name])


def _normalized(row) -> dict:
    return json.loads(row["normalized_payload"])


def test_ingests_and_correlates_official_tool_payloads(tmp_path):
    repo = _initialized_repo(tmp_path)
    db_path = repo / ".agent-quality" / "local" / "quality.sqlite3"
    pre_payload = {
        **_common_payload(repo),
        "toolCall": {
            "name": "run_command",
            "args": {
                "CommandLine": "npm test",
                "Cwd": str(repo),
                "WaitMsBeforeAsync": 5000,
            },
        },
        "stepIdx": 19,
    }
    post_payload = {
        **_common_payload(repo),
        "stepIdx": 19,
        "error": "exit status 1",
    }

    ingest_hook_event("PreToolUse", pre_payload, db_path=db_path, repository_path=repo)
    ingest_hook_event("PostToolUse", post_payload, db_path=db_path, repository_path=repo)

    conn = connect(db_path)
    try:
        rows = all_rows(conn, "SELECT * FROM events ORDER BY rowid")
    finally:
        conn.close()
    assert len(rows) == 2

    pre, post = rows
    assert pre["session_id"] == CONVERSATION_ID
    assert pre["sequence_number"] == 19
    assert pre["tool_category"] == "test"
    assert pre["command"] == "npm test"
    assert pre["path"] == str(repo)
    assert _normalized(pre)["tool_input"] == pre_payload["toolCall"]["args"]

    assert post["session_id"] == CONVERSATION_ID
    assert post["sequence_number"] == 19
    assert post["status"] == "failed"
    assert post["exit_code"] == 1
    assert post["command"] == "npm test"
    assert _normalized(post)["tool_name"] == "run_command"
    assert _normalized(post)["tool_call_id"] == _normalized(pre)["tool_call_id"]
    assert _normalized(post)["error"] == "exit status 1"


def test_installed_post_tool_hook_does_not_require_a_pre_tool_event(tmp_path):
    repo = _initialized_repo(tmp_path)
    db_path = repo / ".agent-quality" / "local" / "quality.sqlite3"
    payload = {**_common_payload(repo), "stepIdx": 5, "error": ""}

    event_id = ingest_hook_event("PostToolUse", payload, db_path=db_path, repository_path=repo)

    conn = connect(db_path)
    try:
        row = one(conn, "SELECT * FROM events WHERE id=?", [event_id])
        session = one(conn, "SELECT * FROM sessions WHERE id=?", [CONVERSATION_ID])
        run_count = conn.execute("SELECT COUNT(*) AS count FROM runs").fetchone()["count"]
    finally:
        conn.close()
    data = _normalized(row)
    assert row["status"] == "success"
    assert row["session_id"] == CONVERSATION_ID
    assert row["sequence_number"] == 5
    assert row["command"] is None
    assert data["error"] is None
    assert session["repository_path"] == str(repo.resolve())
    assert session["task_summary"] is None
    assert run_count == 0


def test_official_invocation_payloads_are_not_treated_as_prompts(tmp_path):
    repo = _initialized_repo(tmp_path)
    db_path = repo / ".agent-quality" / "local" / "quality.sqlite3"
    pre_payload = {
        **_common_payload(repo),
        "invocationNum": 3,
        "initialNumSteps": 10,
    }
    post_payload = {
        **_common_payload(repo),
        "invocationNum": 3,
        "initialNumSteps": 14,
    }

    ingest_hook_event("PreInvocation", pre_payload, db_path=db_path, repository_path=repo)
    ingest_hook_event("PostInvocation", post_payload, db_path=db_path, repository_path=repo)

    conn = connect(db_path)
    try:
        rows = all_rows(conn, "SELECT * FROM events ORDER BY rowid")
        run_count = conn.execute("SELECT COUNT(*) AS count FROM runs").fetchone()["count"]
        session = one(conn, "SELECT * FROM sessions WHERE id=?", [CONVERSATION_ID])
    finally:
        conn.close()
    assert run_count == 0
    assert session["repository_path"] == str(repo.resolve())
    assert session["task_summary"] is None
    assert [row["sequence_number"] for row in rows] == [3, 3]
    assert [_normalized(row)["item_type"] for row in rows] == ["model_invocation", "model_invocation"]
    assert _normalized(rows[0])["initial_num_steps"] == 10
    assert "prompt" not in _normalized(rows[0])


def test_ingests_official_stop_fields(tmp_path):
    repo = _initialized_repo(tmp_path)
    db_path = repo / ".agent-quality" / "local" / "quality.sqlite3"
    payload = {
        **_common_payload(repo),
        "executionNum": 1,
        "terminationReason": "model_stop",
        "error": "",
        "fullyIdle": True,
    }

    event_id = ingest_hook_event("Stop", payload, db_path=db_path, repository_path=repo)

    conn = connect(db_path)
    try:
        row = one(conn, "SELECT * FROM events WHERE id=?", [event_id])
        session = one(conn, "SELECT * FROM sessions WHERE id=?", [CONVERSATION_ID])
    finally:
        conn.close()
    data = _normalized(row)
    assert row["session_id"] == CONVERSATION_ID
    assert row["sequence_number"] == 1
    assert row["status"] == "completed"
    assert data["execution_num"] == 1
    assert data["termination_reason"] == "model_stop"
    assert data["fully_idle"] is True
    assert data["error"] is None
    source = json.loads(row["source_payload_sanitized"])
    assert source["extensions"]["google.antigravity.hook"]["error"] == ""
    assert session["ended_at"] is not None
    assert session["final_outcome"] == "completed"


def test_antigravity_hook_redaction_and_privacy(tmp_path):
    repo = _initialized_repo(tmp_path)
    db_path = repo / ".agent-quality" / "local" / "quality.sqlite3"
    event_id = ingest_hook_event(
        "PreToolUse",
        {
            **_common_payload(repo),
            "toolCall": {
                "name": "run_command",
                "args": {
                    "CommandLine": "echo sk-123456789012345678901234567890123456",
                    "Cwd": str(repo),
                    "secret_key": "ghp_123456789012345678901234567890123456",
                },
            },
            "stepIdx": 2,
        },
        db_path=db_path,
        repository_path=repo,
    )
    conn = connect(db_path)
    try:
        event = one(conn, "SELECT * FROM events WHERE id=?", [event_id])
    finally:
        conn.close()

    assert "ghp_" not in event["source_payload_sanitized"]
    assert "sk-" not in event["source_payload_sanitized"]
    assert "[REDACTED" in event["source_payload_sanitized"]
    assert "sensitive_field" in json.loads(event["redaction_findings"])


def test_antigravity_hook_idempotency_deduplication(tmp_path):
    repo = _initialized_repo(tmp_path)
    db_path = repo / ".agent-quality" / "local" / "quality.sqlite3"
    payload = {
        **_common_payload(repo),
        "invocationNum": 0,
        "initialNumSteps": 1,
    }

    id_1 = ingest_hook_event("PreInvocation", payload, db_path=db_path, repository_path=repo)
    id_2 = ingest_hook_event("PreInvocation", payload, db_path=db_path, repository_path=repo)

    conn = connect(db_path)
    try:
        rows = all_rows(conn, "SELECT * FROM events WHERE session_id=?", [CONVERSATION_ID])
    finally:
        conn.close()
    assert id_2 == id_1
    assert len(rows) == 1
    assert rows[0]["id"] == id_1


def test_unrelated_integrity_error_is_not_misclassified_as_a_duplicate(tmp_path, monkeypatch):
    repo = _initialized_repo(tmp_path)
    db_path = repo / ".agent-quality" / "local" / "quality.sqlite3"
    payload = {
        **_common_payload(repo),
        "invocationNum": 0,
        "initialNumSteps": 1,
    }

    def fail_insert(*args, **kwargs):
        raise sqlite3.IntegrityError("UNIQUE constraint failed: events.id")

    monkeypatch.setattr(antigravity, "insert", fail_insert)

    with pytest.raises(sqlite3.IntegrityError, match="events.id"):
        ingest_hook_event("PreInvocation", payload, db_path=db_path, repository_path=repo)


def test_identifier_free_hook_events_are_not_collapsed(tmp_path):
    repo = _initialized_repo(tmp_path)
    db_path = repo / ".agent-quality" / "local" / "quality.sqlite3"
    payload = {"status": "observed"}

    id_1 = ingest_hook_event("UnknownHook", payload, db_path=db_path, repository_path=repo)
    id_2 = ingest_hook_event("UnknownHook", payload, db_path=db_path, repository_path=repo)

    conn = connect(db_path)
    try:
        rows = all_rows(conn, "SELECT * FROM events ORDER BY rowid")
    finally:
        conn.close()
    assert id_2 != id_1
    assert [row["id"] for row in rows] == [id_1, id_2]


def test_manual_pre_tool_use_response_does_not_auto_allow_a_tool(tmp_path, monkeypatch, capsys):
    repo = _initialized_repo(tmp_path)
    payload = {
        **_common_payload(repo),
        "toolCall": {"name": "view_file", "args": {"AbsolutePath": "/tmp/example"}},
        "stepIdx": 0,
    }

    result = _invoke(monkeypatch, "PreToolUse", payload)

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == {
        "decision": "ask",
        "reason": "Agent Quality telemetry does not make tool permission decisions.",
    }
    assert captured.err == ""


@pytest.mark.parametrize(
    ("event_name", "specific_payload", "expected"),
    [
        ("PostToolUse", {"stepIdx": 0, "error": ""}, {}),
        ("PreInvocation", {"invocationNum": 0, "initialNumSteps": 1}, {}),
        ("PostInvocation", {"invocationNum": 0, "initialNumSteps": 2}, {}),
        (
            "Stop",
            {"executionNum": 0, "terminationReason": "model_stop", "error": "", "fullyIdle": True},
            {"decision": "allow"},
        ),
    ],
)
def test_main_emits_event_valid_stdout_on_success(
    tmp_path, monkeypatch, capsys, event_name, specific_payload, expected
):
    repo = _initialized_repo(tmp_path)
    monkeypatch.delenv("AGENT_QUALITY_HOME", raising=False)
    payload = {**_common_payload(repo), **specific_payload}

    result = _invoke(monkeypatch, event_name, payload)

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == expected
    assert captured.err == ""


@pytest.mark.parametrize(
    ("event_name", "specific_payload", "expected"),
    [
        ("PostToolUse", {"stepIdx": 0, "error": ""}, {}),
        ("PreInvocation", {"invocationNum": 0, "initialNumSteps": 1}, {}),
        ("PostInvocation", {"invocationNum": 0, "initialNumSteps": 2}, {}),
        (
            "Stop",
            {"executionNum": 0, "terminationReason": "model_stop", "error": "", "fullyIdle": True},
            {"decision": "allow"},
        ),
    ],
)
def test_main_emits_event_valid_stdout_when_project_is_skipped(
    tmp_path, monkeypatch, capsys, event_name, specific_payload, expected
):
    repo = tmp_path / "uninitialized"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.delenv("AGENT_QUALITY_HOME", raising=False)
    payload = {**_common_payload(repo), **specific_payload}

    result = _invoke(monkeypatch, event_name, payload)

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == expected
    assert captured.err == ""
    assert not (repo / ".agent-quality").exists()


def test_main_spools_sanitized_payload_and_emits_valid_stdout_on_error(
    tmp_path, monkeypatch, capsys
):
    repo = _initialized_repo(tmp_path)
    monkeypatch.delenv("AGENT_QUALITY_HOME", raising=False)
    payload = {
        **_common_payload(repo),
        "stepIdx": 7,
        "error": "sk-123456789012345678901234567890123456",
    }

    def fail_ingestion(*args, **kwargs):
        raise RuntimeError("simulated ingestion failure")

    monkeypatch.setattr(antigravity, "ingest_hook_event", fail_ingestion)
    result = _invoke(monkeypatch, "PostToolUse", payload)

    captured = capsys.readouterr()
    diagnostic = json.loads(captured.err)
    spool_path = Path(diagnostic["spooled"])
    spool = json.loads(spool_path.read_text(encoding="utf-8"))
    assert result == 0
    assert json.loads(captured.out) == {}
    assert diagnostic["ok"] is False
    assert spool_path.parent == repo / ".agent-quality" / "local" / "spool"
    assert "sk-" not in json.dumps(spool)
    assert "[REDACTED" in json.dumps(spool)
    assert spool["payload"]["conversationId"] == CONVERSATION_ID


def test_main_fails_open_when_spool_write_fails(tmp_path, monkeypatch, capsys):
    repo = _initialized_repo(tmp_path)
    monkeypatch.delenv("AGENT_QUALITY_HOME", raising=False)
    payload = {**_common_payload(repo), "stepIdx": 7, "error": "exit status 1"}

    def fail_ingestion(*args, **kwargs):
        raise RuntimeError("simulated ingestion failure")

    original_open = Path.open

    def fail_spool_open(path, mode="r", *args, **kwargs):
        if path.parent.name == "spool" and "x" in mode:
            raise OSError("read-only spool")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(antigravity, "ingest_hook_event", fail_ingestion)
    monkeypatch.setattr(Path, "open", fail_spool_open)

    result = _invoke(monkeypatch, "PostToolUse", payload)

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == {}
    diagnostic = json.loads(captured.err)
    assert diagnostic == {"ok": False, "spooled": False}


@pytest.mark.parametrize(
    ("event_name", "specific_payload", "expected"),
    [
        ("PostToolUse", {"stepIdx": 0, "error": ""}, {}),
        ("PreInvocation", {"invocationNum": 0, "initialNumSteps": 1}, {}),
        ("PostInvocation", {"invocationNum": 0, "initialNumSteps": 2}, {}),
        (
            "Stop",
            {"executionNum": 0, "terminationReason": "model_stop", "error": "", "fullyIdle": True},
            {"decision": "allow"},
        ),
    ],
)
def test_installed_events_keep_valid_stdout_when_ingestion_fails(
    tmp_path, monkeypatch, capsys, event_name, specific_payload, expected
):
    repo = _initialized_repo(tmp_path)
    payload = {**_common_payload(repo), **specific_payload}

    def fail_ingestion(*args, **kwargs):
        raise RuntimeError("simulated ingestion failure")

    monkeypatch.setattr(antigravity, "ingest_hook_event", fail_ingestion)

    result = _invoke(monkeypatch, event_name, payload)

    captured = capsys.readouterr()
    diagnostic = json.loads(captured.err)
    assert result == 0
    assert json.loads(captured.out) == expected
    assert diagnostic["ok"] is False
    assert Path(diagnostic["spooled"]).is_file()


def test_antigravity_rows_from_jsonl_single_report(tmp_path):
    lines = [
        "{",
        '  "exit_code": 0,',
        '  "output": "The task completed successfully.",',
        '  "usage": {',
        '    "input_tokens": 150,',
        '    "output_tokens": 45',
        "  }",
        "}",
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
