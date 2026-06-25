from __future__ import annotations

import subprocess
from pathlib import Path


def git(repo: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=check)


def repo_root(path: Path) -> Path:
    result = git(path, "rev-parse", "--show-toplevel", check=True)
    return Path(result.stdout.strip())


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
