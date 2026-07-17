from __future__ import annotations

import copy
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast


Provider = Literal["codex", "antigravity"]
ProviderSelection = Literal["codex", "antigravity", "all"]

PROVIDERS: tuple[Provider, ...] = ("codex", "antigravity")

_CODEX_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "Stop",
)
_ANTIGRAVITY_EVENTS = (
    "PostToolUse",
    "PreInvocation",
    "PostInvocation",
    "Stop",
)
_MATCHED_EVENTS = frozenset({"PreToolUse", "PostToolUse", "PermissionRequest"})
_CODEX_MATCHER_EVENTS = frozenset({"SessionStart", *_MATCHED_EVENTS})
_ANTIGRAVITY_MATCHER_EVENTS = frozenset({"PostToolUse"})
_ANTIGRAVITY_NAMESPACE = "agent-quality"
_PYTHON_PROBE_OUTPUT = "agent-quality-hook-runtime-ok"
_IS_WINDOWS = os.name == "nt"


class HookConfigError(ValueError):
    """Raised when an existing hook configuration cannot be safely changed."""


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
    if provider == "codex":
        configured_home = environment.get("CODEX_HOME")
        codex_home = Path(configured_home).expanduser() if configured_home else user_home / ".codex"
        return codex_home.absolute() / "hooks.json"
    if provider == "antigravity":
        return user_home / ".gemini" / "config" / "hooks.json"
    raise ValueError(f"unsupported hook provider: {provider}")


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
        installed = _provider_is_enabled(selected, path, data) and _is_installed(
            selected,
            data,
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
    if provider == "codex" and not _provider_is_enabled(provider, path, original):
        raise HookConfigError(
            f"Codex user hooks are disabled by {path.with_name('config.toml')}; "
            "enable [features].hooks and set allow_managed_hooks_only=false before installing"
        )
    data = copy.deepcopy(original)
    if provider == "codex":
        hooks = data.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            raise HookConfigError(f"expected 'hooks' to be an object in {path}")
        for event, groups in list(hooks.items()):
            if not isinstance(groups, list):
                if event in _CODEX_EVENTS:
                    raise HookConfigError(f"expected hooks.{event} to be an array in {path}")
                continue
            remaining = _without_agent_quality(groups, provider)
            if remaining == groups:
                continue
            if remaining:
                hooks[event] = remaining
            else:
                del hooks[event]
        for event in _CODEX_EVENTS:
            groups = hooks.get(event, [])
            if not isinstance(groups, list):
                raise HookConfigError(f"expected hooks.{event} to be an array in {path}")
            hooks[event] = groups + [_hook_group(python, provider, event)]
    else:
        antigravity_hooks: dict[str, Any] = {"enabled": True}
        for event in _ANTIGRAVITY_EVENTS:
            if event in _ANTIGRAVITY_MATCHER_EVENTS:
                antigravity_hooks[event] = [_hook_group(python, provider, event)]
            else:
                antigravity_hooks[event] = [_hook_handler(python, provider, event)]
        data[_ANTIGRAVITY_NAMESPACE] = antigravity_hooks

    changed = data != original
    return _WritePlan(HookResult(provider, path, True, changed), data if changed else None)


def _plan_uninstall(provider: Provider, path: Path) -> _WritePlan:
    original = _read_json_object(path)
    data = copy.deepcopy(original)
    if provider == "codex":
        hooks = data.get("hooks")
        if hooks is not None and not isinstance(hooks, dict):
            raise HookConfigError(f"expected 'hooks' to be an object in {path}")
        if isinstance(hooks, dict):
            for event, groups in list(hooks.items()):
                if not isinstance(groups, list):
                    continue
                remaining = _without_agent_quality(groups, provider)
                if remaining == groups:
                    continue
                if remaining:
                    hooks[event] = remaining
                else:
                    del hooks[event]
    else:
        data.pop(_ANTIGRAVITY_NAMESPACE, None)

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


def _hook_group(python: Path, provider: Provider, event: str) -> dict[str, Any]:
    group: dict[str, Any] = {"hooks": [_hook_handler(python, provider, event)]}
    if event in _MATCHED_EVENTS:
        group["matcher"] = "*"
    return group


def _hook_handler(python: Path, provider: Provider, event: str) -> dict[str, Any]:
    hook: dict[str, Any] = {
        "type": "command",
        "command": _hook_command(python, provider, event),
    }
    if provider == "codex":
        hook["commandWindows"] = _hook_command_windows(python, provider, event)
    if event == "Stop":
        hook["timeout"] = 30
    return hook


def _hook_command(python: Path, provider: Provider, event: str) -> str:
    if provider == "antigravity" and _IS_WINDOWS:
        return _hook_command_windows(python, provider, event)
    return f"{shlex.quote(str(python))} -m agent_quality.cli hook {provider} {event}"


def _hook_command_windows(python: Path, provider: Provider, event: str) -> str:
    return subprocess.list2cmdline(
        [str(python), "-m", "agent_quality.cli", "hook", provider, event]
    )


def _without_agent_quality(groups: list[Any], provider: Provider) -> list[Any]:
    remaining_groups: list[Any] = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            remaining_groups.append(group)
            continue

        remaining_hooks = [
            hook
            for hook in group["hooks"]
            if not _is_agent_quality_hook(hook, provider)
        ]
        if len(remaining_hooks) == len(group["hooks"]):
            remaining_groups.append(group)
            continue
        if remaining_hooks:
            preserved = copy.deepcopy(group)
            preserved["hooks"] = remaining_hooks
            remaining_groups.append(preserved)
    return remaining_groups


def _is_agent_quality_hook(hook: Any, provider: Provider, event: str | None = None) -> bool:
    if not isinstance(hook, dict) or hook.get("type") != "command":
        return False
    command = hook.get("command")
    if not isinstance(command, str):
        return False
    tokens = _split_hook_command(command, provider)
    if tokens is None:
        return False

    expected = ["-m", "agent_quality.cli", "hook", provider]
    for index in range(len(tokens) - len(expected) + 1):
        if tokens[index : index + len(expected)] != expected:
            continue
        if event is None:
            return True
        event_index = index + len(expected)
        return event_index < len(tokens) and tokens[event_index] == event
    return False


def _provider_is_enabled(provider: Provider, path: Path, data: dict[str, Any]) -> bool:
    if provider == "antigravity":
        hooks = data.get(_ANTIGRAVITY_NAMESPACE)
        return not isinstance(hooks, dict) or hooks.get("enabled") is not False

    config_path = path.with_name("config.toml")
    if not config_path.exists():
        return True
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise HookConfigError(f"cannot read valid TOML from {config_path}: {exc}") from exc
    if config.get("allow_managed_hooks_only") is True:
        return False
    features = config.get("features")
    if not isinstance(features, dict):
        return True
    enabled = features.get("hooks", features.get("codex_hooks", True))
    return enabled is not False


def _is_installed(
    provider: Provider,
    data: dict[str, Any],
) -> bool:
    if provider == "codex":
        hooks = data.get("hooks")
        return isinstance(hooks, dict) and all(
            _grouped_event_is_installed(
                hooks.get(event),
                provider,
                event,
            )
            for event in _CODEX_EVENTS
        )

    hooks = data.get(_ANTIGRAVITY_NAMESPACE)
    if (
        not isinstance(hooks, dict)
        or hooks.get("enabled") is False
        or "PreToolUse" in hooks
    ):
        return False
    return all(
        (
            _grouped_event_is_installed(
                hooks.get(event),
                provider,
                event,
            )
            if event in _ANTIGRAVITY_MATCHER_EVENTS
            else _direct_event_is_installed(
                hooks.get(event),
                provider,
                event,
            )
        )
        for event in _ANTIGRAVITY_EVENTS
    )


def _grouped_event_is_installed(
    groups: Any,
    provider: Provider,
    event: str,
) -> bool:
    if not isinstance(groups, list):
        return False
    return any(
        _is_runnable_agent_quality_hook(
            hook,
            provider,
            event,
        )
        for group in groups
        if (
            isinstance(group, dict)
            and isinstance(group.get("hooks"), list)
            and _matcher_covers_all(provider, event, group.get("matcher"))
        )
        for hook in group["hooks"]
    )


def _direct_event_is_installed(
    handlers: Any,
    provider: Provider,
    event: str,
) -> bool:
    return isinstance(handlers, list) and any(
        _is_runnable_agent_quality_hook(
            handler,
            provider,
            event,
        )
        for handler in handlers
    )


def _matcher_covers_all(provider: Provider, event: str, matcher: Any) -> bool:
    matcher_events = _CODEX_MATCHER_EVENTS if provider == "codex" else _ANTIGRAVITY_MATCHER_EVENTS
    return event not in matcher_events or matcher in (None, "", "*")


def _is_runnable_agent_quality_hook(
    hook: Any,
    provider: Provider,
    event: str,
) -> bool:
    python = _configured_python(hook, provider, event)
    if python is None:
        return False
    # Status is deliberately structural and never executes a command parsed
    # from provider-owned JSON. Installation performs the isolated import probe.
    return python.is_absolute() and python.is_file() and os.access(python, os.X_OK)


def _configured_python(hook: Any, provider: Provider, event: str) -> Path | None:
    if not isinstance(hook, dict) or hook.get("type") != "command":
        return None
    command = hook.get("command")
    if not isinstance(command, str):
        return None
    tokens = _split_hook_command(command, provider)
    if tokens is None:
        return None
    expected_tail = ["-m", "agent_quality.cli", "hook", provider, event]
    if len(tokens) != len(expected_tail) + 1 or tokens[1:] != expected_tail:
        return None
    python = Path(tokens[0])
    if command != _hook_command(python, provider, event):
        return None
    if provider == "codex" and hook.get("commandWindows") != _hook_command_windows(
        python,
        provider,
        event,
    ):
        return None
    return python


def _split_hook_command(command: str, provider: Provider) -> list[str] | None:
    """Split the command using the quoting dialect used when it was generated."""

    windows_command = provider == "antigravity" and _IS_WINDOWS
    try:
        tokens = shlex.split(command, posix=not windows_command)
    except ValueError:
        return None
    if windows_command:
        tokens = [_strip_windows_quotes(token) for token in tokens]
    return tokens


def _strip_windows_quotes(token: str) -> str:
    if len(token) >= 2 and token.startswith('"') and token.endswith('"'):
        return token[1:-1]
    return token


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
