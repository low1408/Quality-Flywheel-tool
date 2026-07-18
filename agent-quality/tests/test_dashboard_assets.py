import json
from pathlib import Path

import pytest

from agent_quality.collector.ui_api import execute_ui_action


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "src" / "agent_quality" / "collector" / "static"
EXTENSION_SOURCE = PROJECT_ROOT / "vscode-extension" / "src" / "extension.js"
EXTENSION_SCRIPTS = PROJECT_ROOT / "vscode-extension" / "scripts"
EXTENSION_GITIGNORE = PROJECT_ROOT / "vscode-extension" / ".gitignore"
PANEL_SOURCE = PROJECT_ROOT / "vscode-extension" / "src" / "dashboard-panel.js"
FLYWHEEL_SOURCE = PROJECT_ROOT / "vscode-extension" / "src" / "flywheel-panel.js"
RUNTIME_SOURCE = PROJECT_ROOT / "vscode-extension" / "src" / "runtime.js"
RUNS_TREE_SOURCE = PROJECT_ROOT / "vscode-extension" / "src" / "runs-tree.js"
EXTENSION_PACKAGE = PROJECT_ROOT / "vscode-extension" / "package.json"


@pytest.mark.parametrize("asset", ["dashboard.html", "dashboard.css", "dashboard.js"])
def test_dashboard_assets_have_one_tracked_source_and_an_explicit_generator(asset):
    assert (STATIC_DIR / asset).is_file()
    source = (EXTENSION_SCRIPTS / "sync-dashboard-assets.js").read_text(encoding="utf-8")
    assert asset in source or "const assets" in source
    assert f"/media/{asset}" in EXTENSION_GITIGNORE.read_text(encoding="utf-8")


def test_extension_entrypoint_is_only_a_composition_root():
    source = EXTENSION_SOURCE.read_text(encoding="utf-8")

    assert len(source.splitlines()) < 150
    assert "String.raw`" not in source
    assert "SELECT " not in source
    for module in ("commands", "dashboard-panel", "flywheel-panel", "runs-tree"):
        assert f'require("./{module}")' in source


def test_repository_discovery_scrubs_inherited_git_selection_environment():
    source = RUNTIME_SOURCE.read_text(encoding="utf-8")
    root_discovery = source.split("function projectRootPath(folder)", 1)[1].split(
        "function commandWorkingDirectory", 1
    )[0]

    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_CEILING_DIRECTORIES",
    ):
        assert f'"{name}"' in source
    assert "delete env[name]" in root_discovery
    assert "env," in root_discovery


def test_dashboard_selection_waits_for_webview_ready_handshake():
    panel = PANEL_SOURCE.read_text(encoding="utf-8")
    dashboard = (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")
    init = dashboard.split("function init()", 1)[1].split(
        "function initSidebarResize", 1
    )[0]

    assert "setTimeout(() => this.selectRun" not in panel
    assert 'message.command === "ready"' in panel
    assert "this.pendingSelection" in panel
    listener = init.index('window.addEventListener("message", handleHostMessage)')
    ready = init.index('vscode.postMessage({ command: "ready" })')
    assert listener < ready


def test_browser_dashboard_retries_authenticated_api_requests_with_session_token():
    source = (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")
    fetch_json = source.split("async function fetchJson", 1)[1].split(
        "function renderError", 1
    )[0]

    assert "window.sessionStorage.getItem(dashboardTokenKey)" in fetch_json
    assert "requestOptions.headers.Authorization = `Bearer ${token}`" in fetch_json
    assert "response.status === 401" in fetch_json
    assert "window.prompt" in fetch_json
    assert "return fetchJson(url, options, false)" in fetch_json


def test_extension_preflights_ui_api_version_and_groups_multi_root_runs():
    runtime = RUNTIME_SOURCE.read_text(encoding="utf-8")
    runs_tree = RUNS_TREE_SOURCE.read_text(encoding="utf-8")
    package = json.loads(EXTENSION_PACKAGE.read_text(encoding="utf-8"))

    run_ui_api = runtime.split("async function runUiApi", 1)[1].split(
        "function ensureUiApiCompatibility", 1
    )[0]
    assert "await ensureUiApiCompatibility(folder)" in run_ui_api
    assert '"--version"' in runtime
    assert "MIN_UI_API_AQ_VERSION" in runtime
    assert "vscode.workspace.workspaceFolders" in runs_tree
    assert "uniqueRepositoryFolders" in runs_tree
    assert "Promise.all" in runs_tree
    assert "class WorkspaceItem" in runs_tree
    assert "test-compatibility.js" in package["scripts"]["test"]
    assert "test-runs-tree.js" in package["scripts"]["test"]


def test_dashboard_keeps_machine_fields_out_of_the_primary_ui():
    source = (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")
    render_runs = source.split("function renderRuns()", 1)[1].split("function filteredRuns()", 1)[0]
    filtered_runs = source.split("function filteredRuns()", 1)[1].split("function renderDetail(", 1)[0]
    overview = source.split("function renderOverview(", 1)[1].split("function renderVerifiers(", 1)[0]

    assert 'class="run-id"' not in render_runs
    assert 'data-run-id="${escapeAttr(run.id)}"' in render_runs
    assert "run.id," in filtered_runs

    for token_label in ("Input tokens", "Cached input", "Output tokens"):
        assert token_label not in overview

    prompt_position = overview.index('aria-labelledby="prompt-heading"')
    feed_position = overview.index('renderExecutionFeed(details, "execution-heading")')
    details_position = overview.index('<details class="overview-secondary">')
    assert prompt_position < feed_position < details_position
    assert "renderExecutionFeed(turn" in overview
    assert "buildChronologicalFeed" in source
    assert "feedTimestamp" in source
    assert "Private chain-of-thought remains encrypted" in source


def test_delete_chat_control_is_vscode_chat_only():
    source = (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")

    assert 'vscode && state.viewMode === "chats"' in source
    assert 'data-action="deleteChat"' in source
    assert 'request("deleteChat", { chat_id: chatId })' in source
    assert 'if (command === "deleteChat")' not in source
    assert 'if (command !== "deleteChat")' in source


def test_dashboard_can_copy_transcript_without_tool_executions():
    source = (STATIC_DIR / "dashboard.js").read_text(encoding="utf-8")

    assert 'data-action="copyNoToolsTranscript"' in source
    assert 'copyTranscript("no-tools")' in source
    assert "chat transcript without tool executions" in source
    assert "full bounded without tool executions" in source
    assert 'includeTools: !excludesTools' in source
    assert 'item.feedType !== "tool_call"' in source


def test_dashboard_details_compacts_large_payloads(tmp_path, monkeypatch):
    from agent_quality.db import connect

    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    db_path = tmp_path / "quality.sqlite3"
    big_text = "x" * 1_200_000
    conn = connect(db_path)
    with conn:
        _insert_run(conn, "run_large", None)
        _insert_event(conn, "evt_large", run_id="run_large", session_id=None)
        conn.execute(
            """
            UPDATE events
            SET normalized_payload=?,
                source_payload_sanitized=?,
                provider_extensions=?
            WHERE id='evt_large'
            """,
            [
                json.dumps(
                    {
                        "assistant_output": big_text,
                        "reasoning": big_text,
                        "tool_input": {"prompt": big_text},
                        "tool_output": big_text,
                    }
                ),
                json.dumps({"raw": big_text}),
                json.dumps({"openai.codex.hook": {"last_assistant_message": big_text}}),
            ],
        )
    conn.close()

    payload = execute_ui_action("details", {"run_id": "run_large"}, db_path=db_path)
    encoded = json.dumps(payload)

    assert len(encoded) < 250_000
    event = payload["events"][0]
    assert len(event["normalized_payload"]) < 2500
    assert "[truncated:" in event["normalized_payload"]
    assert len(event["normalized_payload_json"]["assistant_output"]) < 13000
    assert "[truncated:" in payload["agent_outputs"][0]["text"]
    assert len(payload["agent_outputs"][0]["text"]) < 61000


def test_delete_chat_confirmation_precedes_database_mutation():
    source = PANEL_SOURCE.read_text(encoding="utf-8")
    handler = source.split("async deleteChat(message)", 1)[1].split(
        "authorizedFile(filePath)", 1
    )[0]

    confirmation = handler.index("showWarningMessage")
    cancellation = handler.index("confirmation !== DELETE_CHAT_CONFIRMATION")
    mutation = handler.index('runUiApi(this.folder, "delete_chat"')
    assert confirmation < cancellation < mutation
    assert 'deleted: false' in handler


def test_flywheel_panel_is_vscode_only_and_uses_external_worker():
    source = FLYWHEEL_SOURCE.read_text(encoding="utf-8")
    package = json.loads(EXTENSION_PACKAGE.read_text(encoding="utf-8"))

    assert "class FlywheelPanel" in source
    assert 'shell: false' in source
    assert 'runUiApi(this.folder, "flywheel_candidates")' in source
    assert "this.openRun(message.run_id)" in source
    assert "flywheel.html" not in (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    assert any(command["command"] == "agentQuality.showFlywheel" for command in package["contributes"]["commands"])
    properties = package["contributes"]["configuration"]["properties"]
    assert properties["agentQuality.flywheelJudgeCommand"]["type"] == "array"
    assert properties["agentQuality.flywheelMinClusterSize"]["minimum"] == 2


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

    result = execute_ui_action("delete_chat", {"chat_id": "ses_delete"}, db_path=db_path)

    assert result == {
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

    result = execute_ui_action("delete_chat", {"chat_id": "run_delete"}, db_path=db_path)

    assert result["chat_type"] == "standalone_run"
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

    with pytest.raises(ValueError, match="unknown chat: missing"):
        execute_ui_action("delete_chat", {"chat_id": "missing"}, db_path=db_path)
    conn = connect(db_path)
    assert _ids(conn, "runs") == {"run_keep"}
    assert _ids(conn, "events") == {"evt_keep"}


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
