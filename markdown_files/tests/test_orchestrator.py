import subprocess
import sys
from pathlib import Path

import pytest

from agent_quality.db import all_rows, connect, one
from agent_quality.orchestrator import run_task


def _init_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=path, check=True)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)
    return path


def _json_command(payload: dict) -> list[str]:
    script = f"import json; print(json.dumps({payload!r}))"
    return [sys.executable, "-c", script]


def test_run_task_records_adapter_resulting_commit_and_closed_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    repo = _init_repo(tmp_path / "repo")

    run_id = run_task(
        prompt="record metadata",
        repo=repo,
        agent_command=_json_command({"type": "exec.completed", "command": "pytest -q", "exit_code": 0}),
        agent_timeout_seconds=5,
    )

    row = one(connect(), "SELECT * FROM runs WHERE id=?", [run_id])
    assert row["agent_adapter"] == Path(sys.executable).name
    assert row["resulting_commit"] == subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()
    assert row["agent_status"] == "completed"
    assert row["lifecycle_status"] == "closed"


def test_run_task_times_out_agent_and_closes_run(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    repo = _init_repo(tmp_path / "repo")

    run_id = run_task(
        prompt="timeout",
        repo=repo,
        agent_command=[sys.executable, "-c", "import time; time.sleep(5)"],
        agent_timeout_seconds=0.1,
    )

    row = one(connect(), "SELECT * FROM runs WHERE id=?", [run_id])
    assert row["agent_status"] == "timed_out"
    assert row["lifecycle_status"] == "closed"
    assert row["completed_at"] is not None


def test_run_task_marks_run_closed_when_verifier_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    repo = _init_repo(tmp_path / "repo")

    def fail_verifiers(*args, **kwargs):
        raise RuntimeError("verifier failed")

    monkeypatch.setattr("agent_quality.orchestrator.run_verifiers", fail_verifiers)
    with pytest.raises(RuntimeError):
        run_task(
            prompt="verifier exception",
            repo=repo,
            agent_command=_json_command({"type": "message", "text": "ok"}),
            agent_timeout_seconds=5,
        )

    row = one(connect(), "SELECT * FROM runs")
    assert row["agent_status"] == "failed"
    assert row["lifecycle_status"] == "closed"
    assert row["completed_at"] is not None


def test_explicit_session_turns_increment_and_session_stays_open(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    repo = _init_repo(tmp_path / "repo")
    command = _json_command({"type": "message", "text": "ok"})

    run_task(prompt="turn one", repo=repo, session_id="ses_shared", agent_command=command)
    run_task(prompt="turn two", repo=repo, session_id="ses_shared", agent_command=command)

    conn = connect()
    turns = [row["turn_number"] for row in all_rows(conn, "SELECT turn_number FROM runs ORDER BY turn_number")]
    session = one(conn, "SELECT ended_at FROM sessions WHERE id=?", ["ses_shared"])
    assert turns == [1, 2]
    assert session["ended_at"] is None


def test_protected_path_violation_creates_verifier_result(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    repo = _init_repo(tmp_path / "repo")
    protected = repo / "protected.txt"
    protected.write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "protected.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add protected file"], cwd=repo, check=True, capture_output=True)
    verify = tmp_path / "verify.yaml"
    verify.write_text("version: 1\nprotected_paths:\n  - protected.txt\n", encoding="utf-8")
    script = (
        "from pathlib import Path; import json; "
        "Path('protected.txt').write_text('after\\n'); "
        "print(json.dumps({'type':'message','text':'ok'}))"
    )

    run_id = run_task(
        prompt="change protected",
        repo=repo,
        verify_path=verify,
        agent_command=[sys.executable, "-c", script],
    )

    row = one(
        connect(),
        "SELECT verifier_name, verifier_category, passed FROM verifier_results WHERE run_id=?",
        [run_id],
    )
    assert row["verifier_name"] == "protected-paths"
    assert row["verifier_category"] == "protected_path"
    assert row["passed"] == 0
