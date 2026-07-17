from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from agent_quality.adapters.provider_strategies import ProviderConfigError
from agent_quality.adapters.registry import hook_adapter, hook_provider_names


Provider = str
ProviderSelection = str

PROVIDERS: tuple[Provider, ...] = hook_provider_names()
_ANTIGRAVITY_NAMESPACE = "agent-quality"
_PYTHON_PROBE_OUTPUT = "agent-quality-hook-runtime-ok"
_IS_WINDOWS = os.name == "nt"


HookConfigError = ProviderConfigError


@dataclass(frozen=True)
class HookResult:
    provider: Provider
    path: Path
    installed: bool
    changed: bool = False


@dataclass(frozen=True)
class _WritePlan:
    result: HookResult
    data: dict[str, Any] | None = None


@dataclass(frozen=True)
class _FileSnapshot:
    write_path: Path
    existed: bool
    contents: bytes | None
    mode: int | None


def hook_path(
    provider: Provider,
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return the user-level hook file for a provider."""

    user_home = (home or Path.home()).expanduser().absolute()
    environment = os.environ if environ is None else environ
    return hook_adapter(provider).config_path(user_home, environment)


def install_hooks(
    provider: ProviderSelection = "all",
    python: str = sys.executable,
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[HookResult]:
    """Install Agent Quality hooks into user-level provider configurations."""

    python_path = _absolute_python(python)
    plans = [
        _plan_install(selected, python_path, hook_path(selected, home=home, environ=environ))
        for selected in _providers(provider)
    ]
    return _apply(plans)


def hooks_status(
    provider: ProviderSelection = "all",
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[HookResult]:
    """Report whether every expected Agent Quality hook is installed."""

    results: list[HookResult] = []
    for selected in _providers(provider):
        path = hook_path(selected, home=home, environ=environ)
        data = _read_json_object(path)
        adapter = hook_adapter(selected)
        installed = adapter.is_enabled(path, data) and adapter.is_installed(
            data,
            windows=_IS_WINDOWS,
        )
        results.append(HookResult(selected, path, installed))
    return results


def uninstall_hooks(
    provider: ProviderSelection = "all",
    *,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[HookResult]:
    """Remove only Agent Quality-owned entries from user hook configurations."""

    plans = [
        _plan_uninstall(selected, hook_path(selected, home=home, environ=environ))
        for selected in _providers(provider)
    ]
    return _apply(plans)


def _providers(selection: ProviderSelection) -> tuple[Provider, ...]:
    if selection == "all":
        return PROVIDERS
    if selection in PROVIDERS:
        return (cast(Provider, selection),)
    raise ValueError(f"unsupported hook provider: {selection}")


def _absolute_python(python: str) -> Path:
    value = python.strip()
    if not value:
        raise HookConfigError("Python executable cannot be empty")

    candidate = Path(value).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        path = candidate.absolute()
    else:
        resolved = shutil.which(value)
        if resolved is None:
            raise HookConfigError(f"Python executable was not found: {python}")
        path = Path(resolved).absolute()

    if not path.is_file() or not os.access(path, os.X_OK):
        raise HookConfigError(f"Python executable is not executable: {path}")
    if not _python_can_import_agent_quality(path):
        raise HookConfigError(
            f"Python executable cannot import agent_quality.cli in an isolated process: {path}"
        )
    return path


def _plan_install(provider: Provider, python: Path, path: Path) -> _WritePlan:
    original = _read_json_object(path)
    adapter = hook_adapter(provider)
    data = adapter.prepare_install(original, path, python, windows=_IS_WINDOWS)
    changed = data != original
    return _WritePlan(HookResult(provider, path, True, changed), data if changed else None)


def _plan_uninstall(provider: Provider, path: Path) -> _WritePlan:
    original = _read_json_object(path)
    data = hook_adapter(provider).prepare_uninstall(original, windows=_IS_WINDOWS)
    changed = data != original
    return _WritePlan(HookResult(provider, path, False, changed), data if changed else None)


def _apply(plans: list[_WritePlan]) -> list[HookResult]:
    changed_plans = [plan for plan in plans if plan.data is not None]
    snapshots = {
        plan.result.path: _snapshot_file(plan.result.path)
        for plan in changed_plans
    }
    written: list[Path] = []
    try:
        for plan in changed_plans:
            _atomic_write_json(plan.result.path, cast(dict[str, Any], plan.data))
            written.append(plan.result.path)
    except HookConfigError as exc:
        rollback_errors: list[str] = []
        for path in reversed(written):
            try:
                _restore_snapshot(snapshots[path])
            except HookConfigError as rollback_exc:
                rollback_errors.append(str(rollback_exc))
        if rollback_errors:
            changed = ", ".join(str(path) for path in written)
            raise HookConfigError(
                f"{exc}; rollback failed after changing {changed}: "
                + "; ".join(rollback_errors)
            ) from exc
        if written:
            raise HookConfigError(f"{exc}; earlier provider changes were rolled back") from exc
        raise
    return [plan.result for plan in plans]


def _hook_command(python: Path, provider: Provider, event: str) -> str:
    """Compatibility wrapper for tests and callers of the former helper."""

    return hook_adapter(provider).render_command(
        python,
        event,
        windows=_IS_WINDOWS,
    )


def _python_can_import_agent_quality(python: Path) -> bool:
    if not python.is_file() or not os.access(python, os.X_OK):
        return False
    probe = (
        "import agent_quality.cli,sys;"
        f"sys.stdout.write({_PYTHON_PROBE_OUTPUT!r})"
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    try:
        completed = subprocess.run(
            [str(python), "-P", "-c", probe],
            capture_output=True,
            check=False,
            env=environment,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and completed.stdout == _PYTHON_PROBE_OUTPUT


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HookConfigError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise HookConfigError(f"expected a JSON object in {path}")
    return data


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    contents = (json.dumps(data, indent=2) + "\n").encode("utf-8")
    _atomic_write_bytes(path, contents)


def _snapshot_file(path: Path) -> _FileSnapshot:
    write_path = path.resolve(strict=False) if path.is_symlink() else path
    if not write_path.exists():
        return _FileSnapshot(write_path, False, None, None)
    try:
        return _FileSnapshot(
            write_path,
            True,
            write_path.read_bytes(),
            stat.S_IMODE(write_path.stat().st_mode),
        )
    except OSError as exc:
        raise HookConfigError(f"cannot snapshot {path} before updating it: {exc}") from exc


def _restore_snapshot(snapshot: _FileSnapshot) -> None:
    if snapshot.existed:
        _atomic_write_bytes(
            snapshot.write_path,
            snapshot.contents or b"",
            mode=snapshot.mode,
        )
        return
    try:
        snapshot.write_path.unlink(missing_ok=True)
    except OSError as exc:
        raise HookConfigError(f"cannot remove newly created {snapshot.write_path}: {exc}") from exc


def _atomic_write_bytes(path: Path, contents: bytes, *, mode: int | None = None) -> None:
    write_path = path.resolve(strict=False) if path.is_symlink() else path
    try:
        write_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=write_path.parent,
            prefix=f".{write_path.name}.",
            suffix=".tmp",
        )
    except OSError as exc:
        raise HookConfigError(f"cannot prepare an atomic write for {path}: {exc}") from exc
    temporary_path = Path(temporary_name)
    try:
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
            target_mode = mode
            if target_mode is None and write_path.exists():
                target_mode = stat.S_IMODE(write_path.stat().st_mode)
            if target_mode is not None:
                os.chmod(temporary_path, target_mode)
            os.replace(temporary_path, write_path)
        except OSError as exc:
            raise HookConfigError(f"cannot atomically write {path}: {exc}") from exc
    finally:
        if temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass
