from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_quality.adapters.provider_strategies import resolve_directory
from agent_quality.adapters.registry import hook_adapter
from agent_quality.capture.git_state import discover_repo_root
from agent_quality.ids import new_id
from agent_quality.privacy.redaction import redact_json, redact_text


CONSENT_MARKER_NAME = ".initialized"
SQLITE_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")


@dataclass(frozen=True)
class HookRuntime:
    """Resolved project and storage paths for one globally installed hook."""

    repository_path: Path
    home: Path
    db_path: Path
    spool_dir: Path


def resolve_hook_runtime(
    provider: str,
    payload: dict[str, Any],
    *,
    cwd: Path | str | None = None,
) -> HookRuntime | None:
    """Resolve a hook payload to an opted-in project.

    ``.agent-quality/config.yaml`` and a valid, untracked local consent marker
    must both be present. Provider hooks never use ``AGENT_QUALITY_HOME`` because
    doing so would silently opt every project into telemetry.
    """

    try:
        current_directory = Path.cwd()
        process_cwd = resolve_directory(cwd or current_directory, base=current_directory)
    except (OSError, RuntimeError):
        return None
    candidates = hook_adapter(provider).candidate_directories(payload, process_cwd)
    return next(
        (
            runtime
            for candidate in candidates
            if (runtime := _initialized_runtime(candidate)) is not None
        ),
        None,
    )


def initialize_project_consent(repository_path: Path | str) -> Path:
    """Create or refresh the local consent marker used by global hooks."""

    validate_project_consent_location(repository_path)
    repository = Path(repository_path).expanduser().resolve()
    home = repository / ".agent-quality" / "local"
    marker = home / CONSENT_MARKER_NAME

    home.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{repository}\n", encoding="utf-8")
    return marker


def validate_project_consent_location(repository_path: Path | str) -> None:
    """Reject unsafe or tracked paths before ``aq init`` writes consent state."""

    repository = Path(repository_path).expanduser().resolve()
    agent_quality_dir = repository / ".agent-quality"
    home = agent_quality_dir / "local"
    marker = home / CONSENT_MARKER_NAME
    for path in (agent_quality_dir, home, marker):
        if path.is_symlink():
            raise ValueError(f"refusing to initialize through symlink: {path}")
    if agent_quality_dir.exists() and not agent_quality_dir.is_dir():
        raise ValueError(f"expected a directory: {agent_quality_dir}")
    if home.exists() and not home.is_dir():
        raise ValueError(f"expected a directory: {home}")
    if marker.exists() and not marker.is_file():
        raise ValueError(f"expected a regular consent marker: {marker}")
    if _git_reports_tracked(repository, marker):
        raise ValueError(
            f"refusing to use tracked consent marker; untrack it first: {marker}"
        )


def spool_hook_failure(
    runtime: HookRuntime | None,
    *,
    provider: str,
    event_name: str,
    payload: dict[str, Any],
    error: BaseException,
) -> Path | None:
    """Best-effort, sanitized failure spooling that can never fail a hook."""

    if runtime is None:
        return None
    try:
        if not _runtime_is_safe(runtime):
            return None
        runtime.spool_dir.mkdir(parents=True, exist_ok=True)
        if not _runtime_is_safe(runtime) or not runtime.spool_dir.is_dir():
            return None
        safe_provider = "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in provider
        ).strip("-") or "hook"
        spool_path = runtime.spool_dir / f"{safe_provider}-failed-{new_id('evt')}.json"
        record = {
            "provider": provider,
            "event": event_name,
            "error": redact_text(str(error)).value,
            "payload": redact_json(payload).value,
        }
        with spool_path.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, sort_keys=True)
            stream.write("\n")
        return spool_path
    except Exception:
        return None


def _initialized_runtime(candidate: Path) -> HookRuntime | None:
    repository_path = _repository_root(candidate)
    agent_quality_dir = repository_path / ".agent-quality"
    config = agent_quality_dir / "config.yaml"
    home = agent_quality_dir / "local"
    marker = home / CONSENT_MARKER_NAME
    db_path = home / "quality.sqlite3"
    db_sidecars = _sqlite_sidecar_paths(db_path)
    spool_dir = home / "spool"

    if any(
        path.is_symlink()
        for path in (agent_quality_dir, config, home, marker, db_path, *db_sidecars, spool_dir)
    ):
        return None
    if not config.is_file() or not home.is_dir() or not marker.is_file():
        return None
    try:
        if marker.read_text(encoding="utf-8").strip() != str(repository_path):
            return None
    except (OSError, UnicodeError):
        return None
    if _git_reports_tracked(repository_path, marker):
        return None
    if db_path.exists() and not db_path.is_file():
        return None
    if any(path.exists() and not path.is_file() for path in db_sidecars):
        return None
    if spool_dir.exists() and not spool_dir.is_dir():
        return None

    runtime = HookRuntime(repository_path, home, db_path, spool_dir)
    return runtime if _runtime_is_safe(runtime, check_consent=False) else None


def _runtime_is_safe(runtime: HookRuntime, *, check_consent: bool = True) -> bool:
    try:
        repository = runtime.repository_path.resolve()
    except (OSError, RuntimeError):
        return False
    expected_home = repository / ".agent-quality" / "local"
    expected_db = expected_home / "quality.sqlite3"
    expected_sidecars = _sqlite_sidecar_paths(expected_db)
    expected_spool = expected_home / "spool"
    if (runtime.home, runtime.db_path, runtime.spool_dir) != (expected_home, expected_db, expected_spool):
        return False
    paths = (
        repository / ".agent-quality",
        expected_home,
        expected_home / CONSENT_MARKER_NAME,
        expected_db,
        *expected_sidecars,
        expected_spool,
    )
    if any(path.is_symlink() for path in paths):
        return False
    try:
        if any(not _contains(repository, path.resolve()) for path in paths):
            return False
    except (OSError, RuntimeError):
        return False
    if any(path.exists() and not path.is_file() for path in (expected_db, *expected_sidecars)):
        return False
    if expected_spool.exists() and not expected_spool.is_dir():
        return False
    if not check_consent:
        return True
    validated = _initialized_runtime(repository)
    return validated == runtime


def _git_reports_tracked(repository: Path, marker: Path) -> bool:
    try:
        relative_marker = marker.relative_to(repository)
        result = subprocess.run(
            ["git", "-C", str(repository), "ls-files", "--error-unmatch", "--", str(relative_marker)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=2,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _sqlite_sidecar_paths(db_path: Path) -> tuple[Path, ...]:
    return tuple(db_path.with_name(f"{db_path.name}{suffix}") for suffix in SQLITE_SIDECAR_SUFFIXES)


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _repository_root(candidate: Path) -> Path:
    git_root = discover_repo_root(candidate)
    if git_root is not None:
        return git_root
    for ancestor in (candidate, *candidate.parents):
        if (ancestor / ".agent-quality" / "config.yaml").is_file():
            return ancestor.resolve()
    return candidate.resolve()
