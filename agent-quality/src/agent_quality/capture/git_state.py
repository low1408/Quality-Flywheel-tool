from __future__ import annotations

import os
import subprocess
from pathlib import Path


_REPOSITORY_OVERRIDE_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_GRAFT_FILE",
    "GIT_SHALLOW_FILE",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
)


def git(repo: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name in _REPOSITORY_OVERRIDE_ENV:
        environment.pop(name, None)
    return subprocess.run(
        ["git", "-C", str(_working_directory(repo)), *args],
        text=True,
        capture_output=True,
        check=check,
        env=environment,
    )


def repo_root(path: Path) -> Path:
    root = discover_repo_root(path)
    if root is None:
        raise ValueError(f"not inside a Git repository: {path}")
    return root


def discover_repo_root(path: Path) -> Path | None:
    """Resolve the containing worktree using Git's own discovery rules."""

    try:
        result = git(path, "rev-parse", "--show-toplevel")
    except OSError:
        return None
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        return None
    return Path(value).expanduser().resolve()


def _working_directory(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    return candidate.parent if candidate.is_file() else candidate


def head_commit(repo: Path) -> str:
    result = git(repo, "rev-parse", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def status_porcelain(repo: Path) -> str:
    return git(repo, "status", "--porcelain=v1").stdout


def diff(repo: Path, *args: str) -> str:
    return git(repo, "diff", *args).stdout


def file_hash_if_exists(repo: Path, relative: str) -> str | None:
    from agent_quality.hashutil import sha256_file

    path = repo / relative
    return sha256_file(path) if path.exists() and path.is_file() else None
