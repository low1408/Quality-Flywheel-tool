from __future__ import annotations

import copy
import os
import shlex
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any, Protocol
from urllib.parse import unquote, urlparse


class ProviderConfigError(ValueError):
    """Raised when a provider-owned hook configuration is unsafe to change."""


class HookProviderStrategy(Protocol):
    """Provider-owned configuration, command, and workspace behavior."""

    provider: str
    events: tuple[str, ...]
    grouped_events: frozenset[str]
    emitted_matcher_events: frozenset[str]
    validated_matcher_events: frozenset[str]
    native_windows_command: bool
    requires_windows_command: bool

    def config_path(
        self,
        user_home: Path,
        environ: Mapping[str, str],
    ) -> Path: ...

    def prepare_install(
        self,
        original: dict[str, Any],
        path: Path,
        python: Path,
        *,
        windows: bool,
    ) -> dict[str, Any]: ...

    def prepare_uninstall(
        self,
        original: dict[str, Any],
        *,
        windows: bool,
    ) -> dict[str, Any]: ...

    def is_enabled(self, path: Path, data: dict[str, Any]) -> bool: ...

    def is_installed(self, data: dict[str, Any], *, windows: bool) -> bool: ...

    def render_command(
        self,
        python: PurePath,
        event: str,
        *,
        windows: bool,
    ) -> str: ...

    def candidate_directories(
        self,
        payload: dict[str, Any],
        process_cwd: Path,
    ) -> tuple[Path, ...]: ...

    def trust_note(self, config_path: Path) -> str | None: ...


@dataclass(frozen=True)
class CodexHookStrategy:
    provider: str = "codex"
    events: tuple[str, ...] = (
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PermissionRequest",
        "Stop",
    )
    grouped_events: frozenset[str] = frozenset(
        {
            "SessionStart",
            "UserPromptSubmit",
            "PreToolUse",
            "PostToolUse",
            "PermissionRequest",
            "Stop",
        }
    )
    emitted_matcher_events: frozenset[str] = frozenset(
        {"PreToolUse", "PostToolUse", "PermissionRequest"}
    )
    validated_matcher_events: frozenset[str] = frozenset(
        {"SessionStart", "PreToolUse", "PostToolUse", "PermissionRequest"}
    )
    native_windows_command: bool = False
    requires_windows_command: bool = True

    def config_path(
        self,
        user_home: Path,
        environ: Mapping[str, str],
    ) -> Path:
        configured_home = environ.get("CODEX_HOME")
        codex_home = (
            Path(configured_home).expanduser()
            if configured_home
            else user_home / ".codex"
        )
        return codex_home.absolute() / "hooks.json"

    def prepare_install(
        self,
        original: dict[str, Any],
        path: Path,
        python: Path,
        *,
        windows: bool,
    ) -> dict[str, Any]:
        if not self.is_enabled(path, original):
            raise ProviderConfigError(
                f"Codex user hooks are disabled by {path.with_name('config.toml')}; "
                "enable [features].hooks and set allow_managed_hooks_only=false "
                "before installing"
            )
        data = copy.deepcopy(original)
        hooks = data.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            raise ProviderConfigError(f"expected 'hooks' to be an object in {path}")
        for event, groups in list(hooks.items()):
            if not isinstance(groups, list):
                if event in self.events:
                    raise ProviderConfigError(
                        f"expected hooks.{event} to be an array in {path}"
                    )
                continue
            remaining = _without_owned_groups(groups, self, windows=windows)
            if remaining == groups:
                continue
            if remaining:
                hooks[event] = remaining
            else:
                del hooks[event]
        for event in self.events:
            groups = hooks.get(event, [])
            if not isinstance(groups, list):
                raise ProviderConfigError(
                    f"expected hooks.{event} to be an array in {path}"
                )
            hooks[event] = groups + [_hook_group(self, python, event, windows=windows)]
        return data

    def prepare_uninstall(
        self,
        original: dict[str, Any],
        *,
        windows: bool,
    ) -> dict[str, Any]:
        data = copy.deepcopy(original)
        hooks = data.get("hooks")
        if hooks is not None and not isinstance(hooks, dict):
            raise ProviderConfigError("expected 'hooks' to be an object")
        if isinstance(hooks, dict):
            for event, groups in list(hooks.items()):
                if not isinstance(groups, list):
                    continue
                remaining = _without_owned_groups(groups, self, windows=windows)
                if remaining == groups:
                    continue
                if remaining:
                    hooks[event] = remaining
                else:
                    del hooks[event]
        return data

    def is_enabled(self, path: Path, data: dict[str, Any]) -> bool:
        del data
        config_path = path.with_name("config.toml")
        if not config_path.exists():
            return True
        try:
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise ProviderConfigError(
                f"cannot read valid TOML from {config_path}: {exc}"
            ) from exc
        if config.get("allow_managed_hooks_only") is True:
            return False
        features = config.get("features")
        if not isinstance(features, dict):
            return True
        return features.get("hooks", features.get("codex_hooks", True)) is not False

    def is_installed(self, data: dict[str, Any], *, windows: bool) -> bool:
        hooks = data.get("hooks")
        return isinstance(hooks, dict) and all(
            _grouped_event_is_installed(
                hooks.get(event),
                self,
                event,
                windows=windows,
            )
            for event in self.events
        )

    def render_command(
        self,
        python: PurePath,
        event: str,
        *,
        windows: bool,
    ) -> str:
        del windows
        return _posix_command(python, self.provider, event)

    def candidate_directories(
        self,
        payload: dict[str, Any],
        process_cwd: Path,
    ) -> tuple[Path, ...]:
        values = _payload_values(payload, "cwd") or [process_cwd]
        return _resolved_directories(values, process_cwd)

    def trust_note(self, config_path: Path) -> str | None:
        return (
            "codex: trust is not verified by aq; in a terminal, cd to the "
            "repository and open the interactive Codex CLI (not IDE chat), "
            f"then use /hooks to trust {config_path}; afterward start a new IDE chat"
        )


@dataclass(frozen=True)
class AntigravityHookStrategy:
    provider: str = "antigravity"
    events: tuple[str, ...] = (
        "PostToolUse",
        "PreInvocation",
        "PostInvocation",
        "Stop",
    )
    grouped_events: frozenset[str] = frozenset({"PostToolUse"})
    emitted_matcher_events: frozenset[str] = frozenset({"PostToolUse"})
    validated_matcher_events: frozenset[str] = frozenset({"PostToolUse"})
    native_windows_command: bool = True
    requires_windows_command: bool = False
    namespace: str = "agent-quality"

    def config_path(
        self,
        user_home: Path,
        environ: Mapping[str, str],
    ) -> Path:
        del environ
        return user_home / ".gemini" / "config" / "hooks.json"

    def prepare_install(
        self,
        original: dict[str, Any],
        path: Path,
        python: Path,
        *,
        windows: bool,
    ) -> dict[str, Any]:
        del path
        data = copy.deepcopy(original)
        integration: dict[str, Any] = {"enabled": True}
        for event in self.events:
            handler = _hook_handler(self, python, event, windows=windows)
            integration[event] = (
                [_hook_group(self, python, event, windows=windows)]
                if event in self.grouped_events
                else [handler]
            )
        data[self.namespace] = integration
        return data

    def prepare_uninstall(
        self,
        original: dict[str, Any],
        *,
        windows: bool,
    ) -> dict[str, Any]:
        del windows
        data = copy.deepcopy(original)
        data.pop(self.namespace, None)
        return data

    def is_enabled(self, path: Path, data: dict[str, Any]) -> bool:
        del path
        hooks = data.get(self.namespace)
        return not isinstance(hooks, dict) or hooks.get("enabled") is not False

    def is_installed(self, data: dict[str, Any], *, windows: bool) -> bool:
        hooks = data.get(self.namespace)
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
                    self,
                    event,
                    windows=windows,
                )
                if event in self.grouped_events
                else _direct_event_is_installed(
                    hooks.get(event),
                    self,
                    event,
                    windows=windows,
                )
            )
            for event in self.events
        )

    def render_command(
        self,
        python: PurePath,
        event: str,
        *,
        windows: bool,
    ) -> str:
        if windows:
            return _windows_command(python, self.provider, event)
        return _posix_command(python, self.provider, event)

    def candidate_directories(
        self,
        payload: dict[str, Any],
        process_cwd: Path,
    ) -> tuple[Path, ...]:
        raw_values: list[Any] = []
        for value in _payload_values(payload, "workspacePaths", "workspace_paths"):
            raw_values.extend(value if isinstance(value, list) else [value])
        directories = list(
            _resolved_directories(raw_values or [process_cwd], process_cwd)
        )
        if not directories:
            return (process_cwd,)

        tool_cwd = _antigravity_tool_cwd(payload, process_cwd)
        preferred = [location for location in (tool_cwd, process_cwd) if location]
        ordered: list[Path] = []
        for location in preferred:
            if location not in ordered:
                ordered.append(location)
            ordered.extend(
                directory
                for directory in directories
                if directory not in ordered and _contains(directory, location)
            )
        ordered.extend(directory for directory in directories if directory not in ordered)
        return tuple(ordered)

    def trust_note(self, config_path: Path) -> str | None:
        del config_path
        return None


def resolve_directory(value: Path | str, *, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    resolved = path.resolve()
    return resolved.parent if resolved.is_file() else resolved


def _hook_group(
    strategy: HookProviderStrategy,
    python: Path,
    event: str,
    *,
    windows: bool,
) -> dict[str, Any]:
    group: dict[str, Any] = {
        "hooks": [_hook_handler(strategy, python, event, windows=windows)]
    }
    if event in strategy.emitted_matcher_events:
        group["matcher"] = "*"
    return group


def _hook_handler(
    strategy: HookProviderStrategy,
    python: Path,
    event: str,
    *,
    windows: bool,
) -> dict[str, Any]:
    handler: dict[str, Any] = {
        "type": "command",
        "command": strategy.render_command(python, event, windows=windows),
    }
    if strategy.requires_windows_command:
        handler["commandWindows"] = _windows_command(
            python,
            strategy.provider,
            event,
        )
    if event == "Stop":
        handler["timeout"] = 30
    return handler


def _without_owned_groups(
    groups: list[Any],
    strategy: HookProviderStrategy,
    *,
    windows: bool,
) -> list[Any]:
    remaining_groups: list[Any] = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            remaining_groups.append(group)
            continue
        remaining_hooks = [
            hook
            for hook in group["hooks"]
            if not _owns_hook(hook, strategy, windows=windows)
        ]
        if len(remaining_hooks) == len(group["hooks"]):
            remaining_groups.append(group)
        elif remaining_hooks:
            preserved = copy.deepcopy(group)
            preserved["hooks"] = remaining_hooks
            remaining_groups.append(preserved)
    return remaining_groups


def _owns_hook(
    hook: Any,
    strategy: HookProviderStrategy,
    *,
    windows: bool,
    event: str | None = None,
) -> bool:
    if not isinstance(hook, dict) or hook.get("type") != "command":
        return False
    command = hook.get("command")
    if not isinstance(command, str):
        return False
    tokens = _split_command(command, strategy, windows=windows)
    if tokens is None:
        return False
    expected = ["-m", "agent_quality.cli", "hook", strategy.provider]
    for index in range(len(tokens) - len(expected) + 1):
        if tokens[index : index + len(expected)] != expected:
            continue
        if event is None:
            return True
        event_index = index + len(expected)
        return event_index < len(tokens) and tokens[event_index] == event
    return False


def _grouped_event_is_installed(
    groups: Any,
    strategy: HookProviderStrategy,
    event: str,
    *,
    windows: bool,
) -> bool:
    if not isinstance(groups, list):
        return False
    return any(
        _handler_is_runnable(hook, strategy, event, windows=windows)
        for group in groups
        if (
            isinstance(group, dict)
            and isinstance(group.get("hooks"), list)
            and _matcher_covers_all(strategy, event, group.get("matcher"))
        )
        for hook in group["hooks"]
    )


def _direct_event_is_installed(
    handlers: Any,
    strategy: HookProviderStrategy,
    event: str,
    *,
    windows: bool,
) -> bool:
    return isinstance(handlers, list) and any(
        _handler_is_runnable(handler, strategy, event, windows=windows)
        for handler in handlers
    )


def _matcher_covers_all(
    strategy: HookProviderStrategy,
    event: str,
    matcher: Any,
) -> bool:
    return (
        event not in strategy.validated_matcher_events
        or matcher in (None, "", "*")
    )


def _handler_is_runnable(
    hook: Any,
    strategy: HookProviderStrategy,
    event: str,
    *,
    windows: bool,
) -> bool:
    python = _configured_python(hook, strategy, event, windows=windows)
    return bool(
        python is not None
        and python.is_absolute()
        and python.is_file()
        and os.access(python, os.X_OK)
    )


def _configured_python(
    hook: Any,
    strategy: HookProviderStrategy,
    event: str,
    *,
    windows: bool,
) -> Path | None:
    if not isinstance(hook, dict) or hook.get("type") != "command":
        return None
    command = hook.get("command")
    if not isinstance(command, str):
        return None
    tokens = _split_command(command, strategy, windows=windows)
    if tokens is None:
        return None
    expected_tail = ["-m", "agent_quality.cli", "hook", strategy.provider, event]
    if len(tokens) != len(expected_tail) + 1 or tokens[1:] != expected_tail:
        return None
    python = Path(tokens[0])
    if command != strategy.render_command(python, event, windows=windows):
        return None
    if strategy.requires_windows_command and hook.get(
        "commandWindows"
    ) != _windows_command(python, strategy.provider, event):
        return None
    return python


def _split_command(
    command: str,
    strategy: HookProviderStrategy,
    *,
    windows: bool,
) -> list[str] | None:
    native_windows = strategy.native_windows_command and windows
    try:
        tokens = shlex.split(command, posix=not native_windows)
    except ValueError:
        return None
    if native_windows:
        tokens = [_strip_windows_quotes(token) for token in tokens]
    return tokens


def _strip_windows_quotes(token: str) -> str:
    if len(token) >= 2 and token.startswith('"') and token.endswith('"'):
        return token[1:-1]
    return token


def _posix_command(python: PurePath, provider: str, event: str) -> str:
    return (
        f"{shlex.quote(str(python))} -m agent_quality.cli hook {provider} {event}"
    )


def _windows_command(python: PurePath, provider: str, event: str) -> str:
    return subprocess.list2cmdline(
        [str(python), "-m", "agent_quality.cli", "hook", provider, event]
    )


def _payload_values(payload: dict[str, Any], *keys: str) -> list[Any]:
    direct = [payload[key] for key in keys if key in payload]
    if direct:
        return direct
    found: list[Any] = []
    for value in payload.values():
        if isinstance(value, dict):
            found.extend(_payload_values(value, *keys))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    found.extend(_payload_values(item, *keys))
    return found


def _resolved_directories(values: list[Any], process_cwd: Path) -> tuple[Path, ...]:
    directories: list[Path] = []
    for value in values:
        path_value = _workspace_path(value)
        if path_value is None:
            continue
        directory = resolve_directory(path_value, base=process_cwd)
        if directory not in directories:
            directories.append(directory)
    return tuple(directories or [process_cwd])


def _workspace_path(value: Any) -> str | Path | None:
    if isinstance(value, Path):
        return value
    if isinstance(value, str) and value:
        if value.startswith("file://"):
            parsed = urlparse(value)
            return unquote(parsed.path)
        return value
    if isinstance(value, dict):
        for key in ("path", "uri", "root"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return _workspace_path(candidate)
    return None


def _antigravity_tool_cwd(
    payload: dict[str, Any],
    process_cwd: Path,
) -> Path | None:
    tool_call = payload.get("toolCall") or payload.get("tool_call")
    if not isinstance(tool_call, dict):
        return None
    arguments = tool_call.get("args") or tool_call.get("arguments")
    if not isinstance(arguments, dict):
        return None
    path_value = _workspace_path(arguments.get("Cwd") or arguments.get("cwd"))
    if path_value is None:
        return None
    try:
        return resolve_directory(path_value, base=process_cwd)
    except (OSError, RuntimeError):
        return None


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True
