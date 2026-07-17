from __future__ import annotations

import subprocess
from pathlib import Path

from agent_quality.adapters.hook_runtime import resolve_hook_runtime
from agent_quality.capture.git_state import discover_repo_root, repo_root
from agent_quality.cli import _init_project, _project_root


def _git_repository(path: Path) -> Path:
    path.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return path.resolve()


def test_git_discovery_ignores_empty_nested_dot_git_directory(tmp_path):
    repository = _git_repository(tmp_path / "repository")
    nested = repository / "packages" / "component"
    nested.mkdir(parents=True)
    (nested / ".git").mkdir()

    assert discover_repo_root(nested) == repository
    assert repo_root(nested) == repository
    assert _project_root(nested) == repository


def test_git_discovery_ignores_repository_override_environment(tmp_path, monkeypatch):
    requested = _git_repository(tmp_path / "requested")
    unrelated = _git_repository(tmp_path / "unrelated")
    nested = requested / "nested"
    nested.mkdir()

    monkeypatch.setenv("GIT_DIR", str(unrelated / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(unrelated))

    assert discover_repo_root(nested) == requested.resolve()


def test_git_discovery_accepts_a_file_path(tmp_path):
    repository = _git_repository(tmp_path / "repository")
    source = repository / "src" / "module.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")

    assert discover_repo_root(source) == repository
    assert _project_root(source) == repository


def test_init_and_hook_runtime_share_git_repository_resolution(tmp_path):
    repository = _git_repository(tmp_path / "repository")
    nested = repository / "agent-quality"
    nested.mkdir()
    (nested / ".git").mkdir()

    _init_project(nested)
    runtime = resolve_hook_runtime(
        "codex",
        {"cwd": str(nested), "session_id": "session-1"},
        cwd=nested,
    )

    assert (repository / ".agent-quality" / "config.yaml").is_file()
    assert not (nested / ".agent-quality").exists()
    assert runtime is not None
    assert runtime.repository_path == repository


def test_non_repository_project_root_falls_back_to_requested_directory(tmp_path):
    directory = tmp_path / "plain-directory"
    directory.mkdir()

    assert discover_repo_root(directory) is None
    assert _project_root(directory) == directory.resolve()
