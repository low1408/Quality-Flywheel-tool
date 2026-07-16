import json
from pathlib import Path

import agent_quality.db as aq_db
from agent_quality.collector.envelope import make_envelope, normalize_envelope

from kimi_coding_agent_flywheel.cli import _main
from kimi_coding_agent_flywheel.core.aq_adapter import AQDbAdapter


def _insert_run(conn, run_id: str, *, prompt: str, event_status: str = "failed") -> None:
    aq_db.insert(
        conn,
        "runs",
        {
            "id": run_id,
            "session_id": None,
            "turn_number": 1,
            "prompt": prompt,
            "prompt_hash": f"hash_{run_id}",
            "repository_path": "/tmp/repo",
            "base_commit": "abc123",
            "resulting_commit": None,
            "model": "test-model",
            "agent_adapter": "codex-cli",
            "agent_version": None,
            "wrapper_version": None,
            "codex_config_hash": None,
            "agents_md_hash": None,
            "verifier_version": None,
            "started_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:01:00+00:00",
            "duration_ms": 60_000,
            "agent_status": "failed",
            "verifier_status": "failed",
            "human_status": "rejected",
            "lifecycle_status": "closed",
            "input_tokens": 1,
            "cached_input_tokens": 0,
            "output_tokens": 1,
        },
    )
    aq_db.insert(
        conn,
        "events",
        {
            "id": f"evt_{run_id}",
            "schema_version": "1",
            "event_type": "agent.tool.failed",
            "source_provider": "openai",
            "source_product": "codex",
            "source_event_type": "exec.failed",
            "adapter_version": "1",
            "session_id": None,
            "run_id": run_id,
            "turn_id": None,
            "parent_event_id": None,
            "sequence_number": 1,
            "occurred_at": "2026-01-01T00:00:30+00:00",
            "observed_at": "2026-01-01T00:00:30+00:00",
            "status": event_status,
            "item_type": "command_execution",
            "tool_category": "shell",
            "command": "pytest",
            "exit_code": 1,
            "path": None,
            "duration_ms": 20,
            "normalized_payload": json.dumps({"content": "pytest failed"}),
            "source_payload_sanitized": "{}",
            "provider_extensions": "{}",
            "privacy_status": "sanitized",
            "privacy_policy_version": "1",
            "redaction_findings": "[]",
            "normalization_status": "normalized",
            "idempotency_key": f"key_{run_id}",
        },
    )


def _judge_script(tmp_path: Path, *, fail_on: str | None = None) -> Path:
    path = tmp_path / "judge.py"
    path.write_text(
        "\n".join(
            [
                "import json, os, sys",
                "prompt = sys.stdin.read()",
                "with open(os.environ['CAPTURE_PATH'], 'a', encoding='utf-8') as stream:",
                "    stream.write(prompt + '\\n---\\n')",
                f"if {fail_on!r} and {fail_on!r} in prompt:",
                "    print('judge unavailable', file=sys.stderr)",
                "    raise SystemExit(3)",
                "print(json.dumps({",
                "    'overall_score': 3,",
                "    'failures': [{",
                "        'subcategory': 'skipping_validation',",
                "        'severity': 'high',",
                "        'description': 'Agent skipped validation',",
                "        'root_cause': 'No test verification',",
                "        'suggested_fix': 'Require tests',",
                "        'affected_prompt_component': 'system_prompt',",
                "    }],",
                "    'summary': 'failed',",
                "}))",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _arguments(db_path: Path, judge: Path, *run_ids: str) -> list[str]:
    args = [
        "analyze",
        "--db",
        str(db_path),
        "--min-cluster-size",
        "2",
        "--judge-command-json",
        json.dumps(["python3", str(judge)]),
    ]
    for run_id in run_ids:
        args.extend(["--run-id", run_id])
    return args


def test_analysis_cli_persists_immutable_history_and_redacts_egress(tmp_path, monkeypatch):
    db_path = tmp_path / "quality.sqlite3"
    secret = "sk-proj-123456789012345678901234"
    conn = aq_db.connect(db_path)
    with conn:
        _insert_run(conn, "run_one", prompt=f"Fix one with {secret}")
        _insert_run(conn, "run_two", prompt="Fix two")
    conn.close()
    capture_path = tmp_path / "judge-prompts.txt"
    monkeypatch.setenv("CAPTURE_PATH", str(capture_path))
    judge = _judge_script(tmp_path)

    assert _main(_arguments(db_path, judge, "run_one", "run_two")) == 0
    assert _main(_arguments(db_path, judge, "run_one", "run_two")) == 0

    conn = aq_db.connect(db_path)
    analyses = conn.execute("SELECT * FROM analysis_runs ORDER BY created_at").fetchall()
    assert len(analyses) == 2
    assert all(row["status"] == "completed" for row in analyses)
    assert all(row["failure_count"] == 2 and row["cluster_count"] == 1 for row in analyses)
    assert conn.execute("SELECT COUNT(*) FROM failure_instances").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM failure_clusters").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(DISTINCT analysis_id) FROM failure_instances").fetchone()[0] == 2
    captured = capture_path.read_text(encoding="utf-8")
    assert secret not in captured
    assert "[REDACTED:" in captured


def test_analysis_cli_records_per_run_judge_error_without_failing_job(tmp_path, monkeypatch):
    db_path = tmp_path / "quality.sqlite3"
    conn = aq_db.connect(db_path)
    with conn:
        _insert_run(conn, "run_good", prompt="good task")
        _insert_run(conn, "run_bad", prompt="bad task")
    conn.close()
    monkeypatch.setenv("CAPTURE_PATH", str(tmp_path / "judge-prompts.txt"))
    judge = _judge_script(tmp_path, fail_on="bad task")

    assert _main(_arguments(db_path, judge, "run_good", "run_bad")) == 0

    conn = aq_db.connect(db_path)
    analysis = conn.execute("SELECT * FROM analysis_runs").fetchone()
    assert analysis["status"] == "completed_with_errors"
    inputs = {row["run_id"]: row for row in conn.execute("SELECT * FROM analysis_inputs")}
    assert inputs["run_good"]["status"] == "completed"
    assert inputs["run_bad"]["status"] == "failed"
    assert "judge unavailable" in inputs["run_bad"]["error_message"]


def test_load_run_traces_translates_agent_quality_event(tmp_path):
    db_path = tmp_path / "quality.sqlite3"
    conn = aq_db.connect(db_path)
    with conn:
        _insert_run(conn, "run_trace", prompt="trace task")
    conn.close()

    trace = AQDbAdapter(db_path).load_run_traces(["run_trace"])[0]
    assert trace["task_description"] == "trace task"
    assert trace["events"][0]["event_type"] == "ERROR"
    assert trace["events"][0]["tool_name"] == "pytest"
    assert trace["events"][0]["tool_error"] == "pytest failed"


def test_load_run_traces_translates_codex_hook_and_kimi_events(tmp_path):
    db_path = tmp_path / "quality.sqlite3"
    conn = aq_db.connect(db_path)
    with conn:
        _insert_run(conn, "run_sources", prompt="source task")
        conn.execute("DELETE FROM events WHERE run_id='run_sources'")
        hook = make_envelope(
            event_type="agent.tool.completed",
            source_event_type="PostToolUse",
            run_id="run_sources",
            data={
                "status": "completed",
                "item_type": "mcp_tool_call",
                "tool_category": "mcp",
                "tool_name": "review",
                "tool_input": {"path": "a.py"},
                "tool_output": "reviewed",
            },
            extensions={"openai.codex.hook": {"tool_name": "review"}},
        )
        kimi = make_envelope(
            event_type="agent.tool.started",
            source_event_type="TOOL_CALL",
            run_id="run_sources",
            data={
                "status": "started",
                "item_type": "command_execution",
                "tool_category": "shell",
                "command": "pytest",
                "tool_input": {"args": ["-q"]},
            },
            source_provider="kimi",
            source_product="flywheel",
        )
        aq_db.insert(conn, "events", normalize_envelope(hook))
        aq_db.insert(conn, "events", normalize_envelope(kimi))
    conn.close()

    events = AQDbAdapter(db_path).load_run_traces(["run_sources"])[0]["events"]
    assert [event["event_type"] for event in events] == ["TOOL_RESULT", "TOOL_CALL"]
    assert events[0]["tool_name"] == "review"
    assert events[0]["tool_output"] == "reviewed"
    assert events[1]["tool_input"] == {"args": ["-q"]}


def test_analysis_cli_rejects_unknown_run_without_history(tmp_path, monkeypatch):
    db_path = tmp_path / "quality.sqlite3"
    aq_db.connect(db_path).close()
    monkeypatch.setenv("CAPTURE_PATH", str(tmp_path / "capture.txt"))
    judge = _judge_script(tmp_path)

    assert _main(_arguments(db_path, judge, "missing")) == 2
    conn = aq_db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0] == 0
