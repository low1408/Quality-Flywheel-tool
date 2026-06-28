import json
from pathlib import Path
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEDIA_DIR = PROJECT_ROOT / "vscode-extension" / "media"
STATIC_DIR = PROJECT_ROOT / "src" / "agent_quality" / "collector" / "static"
EXTENSION_SOURCE = PROJECT_ROOT / "vscode-extension" / "src" / "extension.js"


@pytest.mark.parametrize("asset", ["dashboard.html", "dashboard.css", "dashboard.js"])
def test_dashboard_assets_stay_synchronized(asset):
    assert (MEDIA_DIR / asset).read_bytes() == (STATIC_DIR / asset).read_bytes()


def test_dashboard_keeps_machine_fields_out_of_the_primary_ui():
    source = (MEDIA_DIR / "dashboard.js").read_text(encoding="utf-8")
    render_runs = source.split("function renderRuns()", 1)[1].split("function filteredRuns()", 1)[0]
    filtered_runs = source.split("function filteredRuns()", 1)[1].split("function renderDetail(", 1)[0]
    overview = source.split("function renderOverview(", 1)[1].split("function renderVerifiers(", 1)[0]

    assert 'class="run-id"' not in render_runs
    assert 'data-run-id="${escapeAttr(run.id)}"' in render_runs
    assert "run.id," in filtered_runs

    for token_label in ("Input tokens", "Cached input", "Output tokens"):
        assert token_label not in overview

    prompt_position = overview.index('aria-labelledby="prompt-heading"')
    output_position = overview.index('aria-labelledby="output-heading"')
    reasoning_position = overview.index('aria-labelledby="reasoning-heading"')
    tools_position = overview.index('aria-labelledby="tools-heading"')
    details_position = overview.index('<details class="overview-secondary">')
    assert prompt_position < output_position < reasoning_position < tools_position < details_position
    assert "Private chain-of-thought remains encrypted" in overview


def test_delete_chat_control_is_vscode_chat_only():
    source = (MEDIA_DIR / "dashboard.js").read_text(encoding="utf-8")

    assert 'vscode && state.viewMode === "chats"' in source
    assert 'data-action="deleteChat"' in source
    assert 'request("deleteChat", { chat_id: chatId })' in source
    assert 'if (command === "deleteChat")' not in source
    assert 'if (command !== "deleteChat")' in source


def test_delete_chat_confirmation_precedes_database_mutation():
    source = EXTENSION_SOURCE.read_text(encoding="utf-8")
    handler = source.split('if (message.command === "deleteChat")', 1)[1].split(
        'if (message.command === "openFile")', 1
    )[0]

    confirmation = handler.index("showWarningMessage")
    cancellation = handler.index("confirmation !== DELETE_CHAT_CONFIRMATION")
    mutation = handler.index('dashboardDbQuery(folder, "delete_chat"')
    assert confirmation < cancellation < mutation
    assert 'deleted: false' in handler


def test_delete_chat_removes_session_records_but_preserves_files_and_global_metadata(tmp_path, monkeypatch):
    from agent_quality.db import connect

    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    db_path = tmp_path / "quality.sqlite3"
    artifact_path = tmp_path / "artifact.txt"
    artifact_path.write_text("keep me", encoding="utf-8")
    conn = connect(db_path)
    with conn:
        _insert_session(conn, "ses_delete")
        _insert_session(conn, "ses_keep")
        _insert_run(conn, "run_delete_1", "ses_delete")
        _insert_run(conn, "run_delete_2", "ses_delete")
        _insert_run(conn, "run_keep", "ses_keep")
        _insert_event(conn, "evt_delete", run_id="run_delete_1", session_id="ses_delete")
        _insert_event(conn, "evt_delete_session_only", run_id=None, session_id="ses_delete")
        _insert_event(conn, "evt_keep", run_id="run_keep", session_id="ses_keep")
        conn.execute(
            "INSERT INTO artifacts (id, run_id, artifact_type, path, sha256) VALUES (?, ?, ?, ?, ?)",
            ["art_delete", "run_delete_1", "log", str(artifact_path), "sha"],
        )
        conn.execute(
            """
            INSERT INTO verifier_results (id, run_id, verifier_name, verifier_category, passed)
            VALUES ('ver_delete', 'run_delete_1', 'tests', 'test', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO human_reviews (id, run_id, outcome, reviewed_at)
            VALUES ('rev_delete', 'run_delete_1', 'rejected', '2026-01-01T00:00:00.000Z')
            """
        )
        conn.execute(
            """
            INSERT INTO provider_artifacts (
                id, session_id, run_id, source_provider, artifact_type, created_at
            ) VALUES ('provider_delete', 'ses_delete', 'run_delete_1', 'test', 'plan', '2026-01-01T00:00:00.000Z')
            """
        )
        conn.execute(
            """
            INSERT INTO provider_artifact_revisions (
                id, artifact_id, revision_number, payload_sanitized, sha256, created_at
            ) VALUES ('provider_rev_delete', 'provider_delete', 1, '{}', 'sha', '2026-01-01T00:00:00.000Z')
            """
        )
        conn.execute(
            """
            INSERT INTO failure_clusters (id, title, status, occurrence_count)
            VALUES ('cluster_keep', 'Cluster', 'open', 2)
            """
        )
        conn.execute(
            """
            INSERT INTO analysis_runs (id, algorithm, created_at, status)
            VALUES ('analysis_keep', 'test', '2026-01-01T00:00:00.000Z', 'complete')
            """
        )
        conn.execute(
            """
            INSERT INTO failure_cluster_memberships (
                analysis_id, run_id, cluster_id, assignment_type
            ) VALUES ('analysis_keep', 'run_delete_1', 'cluster_keep', 'automatic')
            """
        )
        conn.execute(
            """
            INSERT INTO failure_cluster_memberships (
                analysis_id, run_id, cluster_id, assignment_type
            ) VALUES ('analysis_keep', 'run_keep', 'cluster_keep', 'automatic')
            """
        )
        _insert_failure_instance(conn, "failure_delete", "run_delete_1")
        _insert_failure_instance(conn, "failure_keep", "run_keep")
    conn.close()

    result = _run_dashboard_action(db_path, "delete_chat", {"chat_id": "ses_delete"})

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "chat_id": "ses_delete",
        "chat_type": "session",
        "deleted": True,
        "run_count": 2,
    }
    conn = connect(db_path)
    assert _ids(conn, "sessions") == {"ses_keep"}
    assert _ids(conn, "runs") == {"run_keep"}
    assert _ids(conn, "events") == {"evt_keep"}
    assert _ids(conn, "artifacts") == set()
    assert _ids(conn, "verifier_results") == set()
    assert _ids(conn, "human_reviews") == set()
    assert _ids(conn, "provider_artifacts") == set()
    assert _ids(conn, "provider_artifact_revisions") == set()
    assert _ids(conn, "failure_instances") == {"failure_keep"}
    assert len(conn.execute("SELECT * FROM failure_cluster_memberships").fetchall()) == 1
    assert _ids(conn, "failure_clusters") == {"cluster_keep"}
    assert _ids(conn, "analysis_runs") == {"analysis_keep"}
    assert conn.execute(
        "SELECT occurrence_count FROM failure_clusters WHERE id='cluster_keep'"
    ).fetchone()["occurrence_count"] == 1
    assert artifact_path.read_text(encoding="utf-8") == "keep me"


def test_delete_chat_removes_standalone_run(tmp_path, monkeypatch):
    from agent_quality.db import connect

    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    db_path = tmp_path / "quality.sqlite3"
    conn = connect(db_path)
    with conn:
        _insert_run(conn, "run_delete", None)
        _insert_run(conn, "run_keep", None)
        _insert_event(conn, "evt_delete", run_id="run_delete", session_id=None)
        _insert_event(conn, "evt_keep", run_id="run_keep", session_id=None)
    conn.close()

    result = _run_dashboard_action(db_path, "delete_chat", {"chat_id": "run_delete"})

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["chat_type"] == "standalone_run"
    conn = connect(db_path)
    assert _ids(conn, "runs") == {"run_keep"}
    assert _ids(conn, "events") == {"evt_keep"}


def test_delete_chat_rejects_unknown_id_without_mutation(tmp_path, monkeypatch):
    from agent_quality.db import connect

    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    db_path = tmp_path / "quality.sqlite3"
    conn = connect(db_path)
    with conn:
        _insert_run(conn, "run_keep", None)
        _insert_event(conn, "evt_keep", run_id="run_keep", session_id=None)
    conn.close()

    result = _run_dashboard_action(db_path, "delete_chat", {"chat_id": "missing"})

    assert result.returncode != 0
    assert "unknown chat: missing" in result.stderr
    conn = connect(db_path)
    assert _ids(conn, "runs") == {"run_keep"}
    assert _ids(conn, "events") == {"evt_keep"}


def _dashboard_db_script() -> str:
    source = EXTENSION_SOURCE.read_text(encoding="utf-8")
    return source.split("const DASHBOARD_DB_SCRIPT = String.raw`", 1)[1].split(
        "\n`;\n\nfunction dashboardDbQuery", 1
    )[0]


def _run_dashboard_action(db_path: Path, action: str, payload: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _dashboard_db_script(), str(db_path), action, json.dumps(payload)],
        check=False,
        capture_output=True,
        text=True,
    )


def _insert_session(conn, session_id: str) -> None:
    conn.execute(
        """
        INSERT INTO sessions (id, repository_path, started_at, task_summary)
        VALUES (?, '/repo', '2026-01-01T00:00:00.000Z', 'test chat')
        """,
        [session_id],
    )


def _insert_run(conn, run_id: str, session_id: str | None) -> None:
    conn.execute(
        """
        INSERT INTO runs (
            id, session_id, prompt_hash, repository_path, base_commit,
            agent_adapter, started_at, agent_status
        ) VALUES (?, ?, 'hash', '/repo', 'abc123', 'test', '2026-01-01T00:00:00.000Z', 'completed')
        """,
        [run_id, session_id],
    )


def _insert_event(conn, event_id: str, *, run_id: str | None, session_id: str | None) -> None:
    conn.execute(
        """
        INSERT INTO events (
            id, schema_version, event_type, source_provider, source_event_type,
            adapter_version, session_id, run_id, observed_at, source_payload_sanitized,
            privacy_status, privacy_policy_version, normalization_status
        ) VALUES (?, '1', 'agent.event', 'test', 'test', '1', ?, ?,
                  '2026-01-01T00:00:00.000Z', '{}', 'redacted', '1', 'normalized')
        """,
        [event_id, session_id, run_id],
    )


def _insert_failure_instance(conn, failure_id: str, run_id: str) -> None:
    conn.execute(
        """
        INSERT INTO failure_instances (
            id, run_id, cluster_id, description, severity, timestamp
        ) VALUES (?, ?, 'cluster_keep', 'failure', 'medium', '2026-01-01T00:00:00.000Z')
        """,
        [failure_id, run_id],
    )


def _ids(conn, table: str) -> set[str]:
    return {row["id"] for row in conn.execute(f"SELECT id FROM {table}").fetchall()}
