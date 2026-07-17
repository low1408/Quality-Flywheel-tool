from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_quality.adapters import antigravity, codex_hooks
from agent_quality.adapters.hook_runtime import (
    CONSENT_MARKER_NAME,
    resolve_hook_runtime,
)
from agent_quality.db import all_rows, connect, one


def _initialized_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    (repo / ".git").mkdir(parents=True)
    config = repo / ".agent-quality" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("version: 1\n", encoding="utf-8")
    local = config.parent / "local"
    local.mkdir()
    (local / CONSENT_MARKER_NAME).write_text(f"{repo.resolve()}\n", encoding="utf-8")
    return repo


def _invoke(monkeypatch, main, event_name: str, payload: dict) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return main([event_name])


def test_codex_hook_routes_from_payload_cwd_and_records_canonical_root(tmp_path, monkeypatch, capsys):
    repo = _initialized_repo(tmp_path, "repo")
    nested = repo / "src" / "package"
    nested.mkdir(parents=True)
    launcher = tmp_path / "launcher"
    launcher.mkdir()
    fake_home = tmp_path / "home"
    monkeypatch.chdir(launcher)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("AGENT_QUALITY_HOME", raising=False)

    result = _invoke(
        monkeypatch,
        codex_hooks.main,
        "UserPromptSubmit",
        {
            "cwd": str(nested),
            "event_id": "evt_routed_codex",
            "session_id": "ses_routed_codex",
            "prompt": "route this event",
        },
    )

    assert result == 0
    assert capsys.readouterr().out == ""
    db_path = repo / ".agent-quality" / "local" / "quality.sqlite3"
    conn = connect(db_path)
    try:
        run = one(conn, "SELECT repository_path FROM runs")
        session = one(conn, "SELECT repository_path FROM sessions")
    finally:
        conn.close()
    assert run["repository_path"] == str(repo.resolve())
    assert session["repository_path"] == str(repo.resolve())
    assert not (launcher / ".agent-quality").exists()
    assert not (fake_home / ".agent-quality").exists()


def test_antigravity_hook_selects_initialized_workspace_path(tmp_path, monkeypatch, capsys):
    repo = _initialized_repo(tmp_path, "repo")
    nested = repo / "packages" / "app"
    nested.mkdir(parents=True)
    uninitialized = tmp_path / "other"
    uninitialized.mkdir()
    launcher = tmp_path / "launcher"
    launcher.mkdir()
    monkeypatch.chdir(launcher)
    monkeypatch.delenv("AGENT_QUALITY_HOME", raising=False)

    result = _invoke(
        monkeypatch,
        antigravity.main,
        "PreInvocation",
        {
            "workspacePaths": [str(uninitialized), str(nested)],
            "conversationId": "ses_routed_antigravity",
            "invocationNum": 0,
            "initialNumSteps": 0,
        },
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {}
    conn = connect(repo / ".agent-quality" / "local" / "quality.sqlite3")
    try:
        event = one(conn, "SELECT session_id FROM events")
        session = one(conn, "SELECT repository_path FROM sessions")
    finally:
        conn.close()
    assert event["session_id"] == "ses_routed_antigravity"
    assert session["repository_path"] == str(repo.resolve())
    assert not (uninitialized / ".agent-quality").exists()


def test_antigravity_prefers_the_workspace_containing_process_cwd(tmp_path, monkeypatch, capsys):
    first = _initialized_repo(tmp_path, "first")
    second = _initialized_repo(tmp_path, "second")
    nested = second / "packages" / "app"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.delenv("AGENT_QUALITY_HOME", raising=False)

    result = _invoke(
        monkeypatch,
        antigravity.main,
        "PreInvocation",
        {
            "workspacePaths": [str(first), str(second)],
            "conversationId": "ses_multi_workspace",
            "invocationNum": 0,
            "initialNumSteps": 0,
        },
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {}
    assert not (first / ".agent-quality" / "local" / "quality.sqlite3").exists()
    conn = connect(second / ".agent-quality" / "local" / "quality.sqlite3")
    try:
        event = one(conn, "SELECT session_id FROM events")
        session = one(conn, "SELECT repository_path FROM sessions")
    finally:
        conn.close()
    assert event["session_id"] == "ses_multi_workspace"
    assert session["repository_path"] == str(second.resolve())


def test_hook_uses_process_cwd_when_provider_path_is_absent(tmp_path, monkeypatch, capsys):
    repo = _initialized_repo(tmp_path, "repo")
    nested = repo / "src"
    nested.mkdir()
    monkeypatch.chdir(nested)
    monkeypatch.delenv("AGENT_QUALITY_HOME", raising=False)

    result = _invoke(
        monkeypatch,
        codex_hooks.main,
        "PostToolUse",
        {"session_id": "ses_cwd", "tool_name": "Bash", "command": "true", "exit_code": 0},
    )

    assert result == 0
    assert capsys.readouterr().out == ""
    assert (repo / ".agent-quality" / "local" / "quality.sqlite3").is_file()


def test_uninitialized_project_is_skipped_without_writes(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "uninitialized"
    (repo / ".git").mkdir(parents=True)
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("AGENT_QUALITY_HOME", raising=False)

    result = _invoke(
        monkeypatch,
        codex_hooks.main,
        "PostToolUse",
        {"cwd": str(repo), "session_id": "ses_skipped", "command": "true"},
    )

    assert result == 0
    assert capsys.readouterr().out == ""
    assert not (repo / ".agent-quality").exists()
    assert not (fake_home / ".agent-quality").exists()


def test_explicit_home_does_not_bypass_project_opt_in(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "configured-only"
    (repo / ".git").mkdir(parents=True)
    config = repo / ".agent-quality" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("version: 1\n", encoding="utf-8")
    aq_home = tmp_path / "custom-agent-quality-home"
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(aq_home))

    result = _invoke(
        monkeypatch,
        codex_hooks.main,
        "UserPromptSubmit",
        {
            "cwd": str(repo),
            "event_id": "evt_explicit_home",
            "session_id": "ses_explicit_home",
            "prompt": "capture with an override",
        },
    )

    assert result == 0
    assert capsys.readouterr().out == ""
    assert not (aq_home / "quality.sqlite3").exists()
    assert not (repo / ".agent-quality" / "local").exists()


def test_consent_marker_must_be_bound_to_the_canonical_repository(tmp_path):
    repo = _initialized_repo(tmp_path, "repo")
    marker = repo / ".agent-quality" / "local" / CONSENT_MARKER_NAME
    marker.write_text(f"{tmp_path / 'different-repository'}\n", encoding="utf-8")

    assert resolve_hook_runtime("codex", {"cwd": str(repo)}, cwd=repo) is None
    assert not (repo / ".agent-quality" / "local" / "quality.sqlite3").exists()


def test_tracked_consent_marker_does_not_opt_in_repository(tmp_path):
    repo = tmp_path / "tracked-marker"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(repo)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    config = repo / ".agent-quality" / "config.yaml"
    local = config.parent / "local"
    local.mkdir(parents=True)
    config.write_text("version: 1\n", encoding="utf-8")
    marker = local / CONSENT_MARKER_NAME
    marker.write_text(f"{repo.resolve()}\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "-f", str(config.relative_to(repo)), str(marker.relative_to(repo))],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    assert resolve_hook_runtime("codex", {"cwd": str(repo)}, cwd=repo) is None


@pytest.mark.parametrize("unsafe_component", ["agent_quality", "local", "marker", "db", "spool"])
def test_symlinked_runtime_components_are_rejected(tmp_path, unsafe_component):
    repo = tmp_path / f"repo-{unsafe_component}"
    (repo / ".git").mkdir(parents=True)
    external = tmp_path / f"external-{unsafe_component}"

    if unsafe_component == "agent_quality":
        external.mkdir()
        (external / "config.yaml").write_text("version: 1\n", encoding="utf-8")
        local = external / "local"
        local.mkdir()
        (local / CONSENT_MARKER_NAME).write_text(f"{repo.resolve()}\n", encoding="utf-8")
        (repo / ".agent-quality").symlink_to(external, target_is_directory=True)
    else:
        aq = repo / ".agent-quality"
        aq.mkdir()
        (aq / "config.yaml").write_text("version: 1\n", encoding="utf-8")
        local = aq / "local"
        if unsafe_component == "local":
            external.mkdir()
            (external / CONSENT_MARKER_NAME).write_text(f"{repo.resolve()}\n", encoding="utf-8")
            local.symlink_to(external, target_is_directory=True)
        else:
            local.mkdir()
            marker = local / CONSENT_MARKER_NAME
            if unsafe_component == "marker":
                external.write_text(f"{repo.resolve()}\n", encoding="utf-8")
                marker.symlink_to(external)
            else:
                marker.write_text(f"{repo.resolve()}\n", encoding="utf-8")
                target = local / ("quality.sqlite3" if unsafe_component == "db" else "spool")
                if unsafe_component == "db":
                    external.write_text("unchanged", encoding="utf-8")
                    target.symlink_to(external)
                else:
                    external.mkdir()
                    target.symlink_to(external, target_is_directory=True)

    assert resolve_hook_runtime("codex", {"cwd": str(repo)}, cwd=repo) is None


@pytest.mark.parametrize(
    "sidecar_name",
    ["quality.sqlite3-journal", "quality.sqlite3-wal", "quality.sqlite3-shm"],
)
@pytest.mark.parametrize("unsafe_kind", ["symlink", "directory"])
def test_unsafe_sqlite_sidecars_are_rejected(tmp_path, sidecar_name, unsafe_kind):
    repo = _initialized_repo(tmp_path, f"repo-{unsafe_kind}-{sidecar_name}")
    sidecar = repo / ".agent-quality" / "local" / sidecar_name

    if unsafe_kind == "symlink":
        external = tmp_path / f"external-{sidecar_name}"
        external.write_text("unchanged", encoding="utf-8")
        sidecar.symlink_to(external)
    else:
        sidecar.mkdir()

    assert resolve_hook_runtime("codex", {"cwd": str(repo)}, cwd=repo) is None


def test_antigravity_tool_cwd_takes_precedence_over_process_cwd(tmp_path):
    first = _initialized_repo(tmp_path, "first")
    second = _initialized_repo(tmp_path, "second")
    nested = second / "packages" / "app"
    nested.mkdir(parents=True)

    runtime = resolve_hook_runtime(
        "antigravity",
        {
            "workspacePaths": [str(first), str(second)],
            "toolCall": {"name": "run_command", "args": {"Cwd": str(nested)}},
        },
        cwd=first,
    )

    assert runtime is not None
    assert runtime.repository_path == second.resolve()


def test_antigravity_tool_cwd_prefers_nested_repository_over_workspace_root(tmp_path):
    outer = _initialized_repo(tmp_path, "outer")
    nested = _initialized_repo(outer, "nested")

    runtime = resolve_hook_runtime(
        "antigravity",
        {
            "workspacePaths": [str(outer)],
            "toolCall": {"name": "run_command", "args": {"Cwd": str(nested)}},
        },
        cwd=outer,
    )

    assert runtime is not None
    assert runtime.repository_path == nested.resolve()


def test_global_hooks_keep_initialized_projects_in_separate_databases(tmp_path, monkeypatch, capsys):
    first = _initialized_repo(tmp_path, "first")
    second = _initialized_repo(tmp_path, "second")
    monkeypatch.delenv("AGENT_QUALITY_HOME", raising=False)

    for index, repo in enumerate((first, second), start=1):
        result = _invoke(
            monkeypatch,
            codex_hooks.main,
            "PostToolUse",
            {"cwd": str(repo), "session_id": f"ses_{index}", "command": f"echo {index}"},
        )
        assert result == 0
    capsys.readouterr()

    for index, repo in enumerate((first, second), start=1):
        conn = connect(repo / ".agent-quality" / "local" / "quality.sqlite3")
        try:
            events = all_rows(conn, "SELECT session_id, command FROM events")
        finally:
            conn.close()
        assert [(row["session_id"], row["command"]) for row in events] == [(f"ses_{index}", f"echo {index}")]


def test_hook_failures_spool_inside_resolved_project(tmp_path, monkeypatch, capsys):
    repo = _initialized_repo(tmp_path, "repo")
    monkeypatch.delenv("AGENT_QUALITY_HOME", raising=False)

    secret = "sk-123456789012345678901234567890"

    def fail_ingestion(*args, **kwargs):
        raise RuntimeError(f"simulated ingestion failure {secret}")

    monkeypatch.setattr(codex_hooks, "ingest_hook_event", fail_ingestion)
    payload = {"cwd": str(repo), "secret_key": secret}
    result = _invoke(monkeypatch, codex_hooks.main, "PostToolUse", payload)

    assert result == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    output = json.loads(captured.err)
    spool_path = Path(output["spooled"])
    assert output["ok"] is False
    assert spool_path.parent == repo / ".agent-quality" / "local" / "spool"
    assert spool_path.is_file()
    record = json.loads(spool_path.read_text(encoding="utf-8"))
    assert record["event"] == "PostToolUse"
    assert record["payload"] == {"cwd": str(repo), "secret_key": "[REDACTED:field]"}
    assert record["error"] == "simulated ingestion failure [REDACTED:openai_api_key]"
    assert secret not in spool_path.read_text(encoding="utf-8")


def test_codex_hook_remains_fail_open_when_spooling_fails(tmp_path, monkeypatch, capsys):
    repo = _initialized_repo(tmp_path, "repo")

    def fail_ingestion(*args, **kwargs):
        raise RuntimeError("simulated ingestion failure")

    def fail_spool(*args, **kwargs):
        raise OSError("simulated spool failure")

    monkeypatch.setattr(codex_hooks, "ingest_hook_event", fail_ingestion)
    monkeypatch.setattr(codex_hooks, "spool_hook_failure", fail_spool)

    result = _invoke(monkeypatch, codex_hooks.main, "PostToolUse", {"cwd": str(repo)})

    assert result == 0
    assert capsys.readouterr().out == ""


def test_explicit_database_creates_only_its_parent(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    database = tmp_path / "project" / ".agent-quality" / "local" / "quality.sqlite3"
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("AGENT_QUALITY_HOME", raising=False)

    conn = connect(database)
    conn.close()

    assert database.is_file()
    assert not (fake_home / ".agent-quality").exists()


def test_codex_hook_events_without_occurrence_ids_are_not_deduplicated(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    payload = {
        "threadId": "thr_codex_dedupe",
        "toolName": "Bash",
        "command": "echo ok",
        "exitCode": 0,
    }

    first_id = codex_hooks.ingest_hook_event("PostToolUse", payload)
    second_id = codex_hooks.ingest_hook_event("PostToolUse", payload)

    conn = connect()
    try:
        events = all_rows(conn, "SELECT id, idempotency_key FROM events")
    finally:
        conn.close()
    assert second_id != first_id
    assert len(events) == 2
    assert all(not row["idempotency_key"].startswith("codex-hook:") for row in events)


def test_codex_hook_events_with_provider_occurrence_ids_are_deduplicated(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    payload = {
        "session_id": "ses_codex_dedupe",
        "turn_id": "turn_codex_dedupe",
        "tool_use_id": "call_codex_dedupe",
        "tool_name": "Bash",
        "tool_input": {"command": "echo ok"},
    }

    first_id = codex_hooks.ingest_hook_event("PostToolUse", payload)
    second_id = codex_hooks.ingest_hook_event("PostToolUse", payload)

    conn = connect()
    try:
        events = all_rows(conn, "SELECT id, idempotency_key FROM events")
    finally:
        conn.close()
    assert second_id == first_id
    assert len(events) == 1
    assert events[0]["idempotency_key"].startswith("codex-hook:")


def test_codex_call_id_is_a_stable_tool_occurrence_id(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    payload = {
        "session_id": "ses_codex_call_id",
        "turn_id": "turn_codex_call_id",
        "call_id": "call_codex_stable",
        "tool_name": "Bash",
    }

    first_id = codex_hooks.ingest_hook_event("PostToolUse", payload)
    second_id = codex_hooks.ingest_hook_event("PostToolUse", payload)

    assert second_id == first_id
    assert len(all_rows(connect(), "SELECT id FROM events")) == 1


def test_codex_permission_request_uses_tool_occurrence_id_for_deduplication(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    payload = {
        "session_id": "ses_permission_dedupe",
        "turn_id": "turn_permission_dedupe",
        "tool_use_id": "call_permission_dedupe",
        "tool_name": "Bash",
        "tool_input": {"command": "git push"},
    }

    first_id = codex_hooks.ingest_hook_event("PermissionRequest", payload)
    second_id = codex_hooks.ingest_hook_event("PermissionRequest", payload)

    conn = connect()
    try:
        events = all_rows(conn, "SELECT id FROM events")
    finally:
        conn.close()
    assert second_id == first_id
    assert len(events) == 1
