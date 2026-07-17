from __future__ import annotations

import importlib
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePath
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from agent_quality.adapters.provider_strategies import (
    AntigravityHookStrategy,
    CodexHookStrategy,
    HookProviderStrategy,
)


STREAM_CAPABILITIES = {
    "prompt_submitted": True,
    "assistant_output": True,
    "reasoning_summaries": True,
    "tool_started": True,
    "tool_completed": True,
    "file_mutations": True,
    "artifact_events": False,
    "token_usage": True,
}
CODEX_CLI_CAPABILITIES = dict(STREAM_CAPABILITIES)
ANTIGRAVITY_CAPABILITIES = dict(STREAM_CAPABILITIES)


@runtime_checkable
class StreamAdapter(Protocol):
    """Command construction and event parsing for one agent stream format."""

    name: str
    aliases: tuple[str, ...]
    executables: tuple[str, ...]
    capabilities: Mapping[str, bool]
    version_executable: str

    def prepare_command(
        self,
        command: list[str] | None,
        *,
        prompt: str,
        model: str | None,
    ) -> list[str]: ...

    def rows_from_jsonl(
        self,
        lines: Iterable[str],
        *,
        run_id: str,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def extract_usage(
        self,
        raw_lines: Iterable[str],
    ) -> tuple[int | None, int | None, int | None]: ...

    def version(self, executable: str | None = None) -> str | None: ...


@runtime_checkable
class HookProviderAdapter(Protocol):
    """Hook installation, routing, runtime discovery, and payload projection."""

    hook_provider: str
    hook_extension_key: str
    hook_agent_adapter: str
    hook_events: tuple[str, ...]
    hook_grouped_events: frozenset[str]
    hook_matcher_events: frozenset[str]

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

    def dispatch_hook(self, event: str) -> int: ...

    def hook_payload(
        self,
        extensions: Mapping[str, Any],
    ) -> dict[str, Any] | None: ...

    def assistant_output(
        self,
        event: str,
        payload: dict[str, Any],
    ) -> str | None: ...

    def file_links(
        self,
        payload: dict[str, Any],
        assistant_output: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def artifacts(self, payload: dict[str, Any]) -> list[dict[str, Any]]: ...


# Backward-compatible type names for callers that imported the first registry draft.
AdapterProtocol = StreamAdapter
HookAdapterProtocol = HookProviderAdapter


class CommandBuilder(Protocol):
    def __call__(
        self,
        command: list[str] | None,
        *,
        prompt: str,
        model: str | None,
    ) -> list[str]: ...


@dataclass(frozen=True)
class BuiltinStreamAdapter:
    name: str
    aliases: tuple[str, ...]
    executables: tuple[str, ...]
    parser_module: str
    capabilities: Mapping[str, bool]
    command_builder: CommandBuilder
    version_executable: str

    def prepare_command(
        self,
        command: list[str] | None,
        *,
        prompt: str,
        model: str | None,
    ) -> list[str]:
        return self.command_builder(command, prompt=prompt, model=model)

    def rows_from_jsonl(
        self,
        lines: Iterable[str],
        *,
        run_id: str,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._parser_function("rows_from_jsonl")(
            lines,
            run_id=run_id,
            session_id=session_id,
        )

    def extract_usage(
        self,
        raw_lines: Iterable[str],
    ) -> tuple[int | None, int | None, int | None]:
        return self._parser_function("extract_usage")(raw_lines)

    def version(self, executable: str | None = None) -> str | None:
        executable = executable or self.version_executable
        candidate = Path(executable).expanduser()
        if candidate.parent == Path("."):
            resolved = shutil.which(executable)
            if resolved is None:
                return None
            executable = resolved
        elif not candidate.is_file():
            return None
        try:
            process = subprocess.run(
                [executable, "--version"],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return (process.stdout or process.stderr).strip() or None

    def _parser_function(self, name: str) -> Any:
        return getattr(importlib.import_module(self.parser_module), name)


@dataclass(frozen=True)
class BuiltinHookProvider:
    hook_provider: str
    hook_module: str
    hook_extension_key: str
    hook_agent_adapter: str
    strategy: HookProviderStrategy

    @property
    def hook_events(self) -> tuple[str, ...]:
        return self.strategy.events

    @property
    def hook_grouped_events(self) -> frozenset[str]:
        return self.strategy.grouped_events

    @property
    def hook_matcher_events(self) -> frozenset[str]:
        return self.strategy.emitted_matcher_events

    def config_path(
        self,
        user_home: Path,
        environ: Mapping[str, str],
    ) -> Path:
        return self.strategy.config_path(user_home, environ)

    def prepare_install(
        self,
        original: dict[str, Any],
        path: Path,
        python: Path,
        *,
        windows: bool,
    ) -> dict[str, Any]:
        return self.strategy.prepare_install(
            original,
            path,
            python,
            windows=windows,
        )

    def prepare_uninstall(
        self,
        original: dict[str, Any],
        *,
        windows: bool,
    ) -> dict[str, Any]:
        return self.strategy.prepare_uninstall(original, windows=windows)

    def is_enabled(self, path: Path, data: dict[str, Any]) -> bool:
        return self.strategy.is_enabled(path, data)

    def is_installed(self, data: dict[str, Any], *, windows: bool) -> bool:
        return self.strategy.is_installed(data, windows=windows)

    def render_command(
        self,
        python: PurePath,
        event: str,
        *,
        windows: bool,
    ) -> str:
        return self.strategy.render_command(python, event, windows=windows)

    def candidate_directories(
        self,
        payload: dict[str, Any],
        process_cwd: Path,
    ) -> tuple[Path, ...]:
        return self.strategy.candidate_directories(payload, process_cwd)

    def trust_note(self, config_path: Path) -> str | None:
        return self.strategy.trust_note(config_path)

    def dispatch_hook(self, event: str) -> int:
        return int(self._hook_function("main")([event]))

    def hook_payload(self, extensions: Mapping[str, Any]) -> dict[str, Any] | None:
        payload = extensions.get(self.hook_extension_key)
        return payload if isinstance(payload, dict) else None

    def assistant_output(self, event: str, payload: dict[str, Any]) -> str | None:
        return self._hook_function("assistant_output")(event, payload)

    def file_links(
        self,
        payload: dict[str, Any],
        assistant_output: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._hook_function("file_links")(payload, assistant_output)

    def artifacts(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        return self._hook_function("artifacts")(payload)

    def _hook_function(self, name: str) -> Any:
        return getattr(importlib.import_module(self.hook_module), name)


@dataclass(frozen=True)
class AdapterSelection:
    adapter: StreamAdapter
    command: list[str]
    reported_name: str
    version_executable: str

    def version(self) -> str | None:
        return self.adapter.version(self.version_executable)


def _codex_command(
    command: list[str] | None,
    *,
    prompt: str,
    model: str | None,
) -> list[str]:
    if command is not None:
        return list(command)
    result = ["codex", "exec", "--json"]
    if model:
        result.extend(("--model", model))
    result.extend(("--sandbox", "workspace-write", prompt))
    return result


def _antigravity_command(
    command: list[str] | None,
    *,
    prompt: str,
    model: str | None,
) -> list[str]:
    del model
    if command is None:
        return ["antigravity", "chat", prompt]
    result = list(command)
    if len(result) != 1:
        return result
    executable = Path(result[0]).name
    if executable == "agy":
        return [
            result[0],
            "-p",
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
            prompt,
        ]
    if executable == "antigravity":
        return [result[0], "chat", prompt]
    return result


CODEX_STREAM_ADAPTER = BuiltinStreamAdapter(
    name="codex-cli",
    aliases=("codex",),
    executables=("codex",),
    parser_module="agent_quality.adapters.codex_cli",
    capabilities=MappingProxyType(CODEX_CLI_CAPABILITIES),
    command_builder=_codex_command,
    version_executable="codex",
)
ANTIGRAVITY_STREAM_ADAPTER = BuiltinStreamAdapter(
    name="antigravity",
    aliases=("agy",),
    executables=("antigravity", "agy"),
    parser_module="agent_quality.adapters.antigravity",
    capabilities=MappingProxyType(ANTIGRAVITY_CAPABILITIES),
    command_builder=_antigravity_command,
    version_executable="antigravity",
)

CODEX_HOOK_PROVIDER = BuiltinHookProvider(
    hook_provider="codex",
    hook_module="agent_quality.adapters.codex_hooks",
    hook_extension_key="openai.codex.hook",
    hook_agent_adapter="codex-hooks",
    strategy=CodexHookStrategy(),
)
ANTIGRAVITY_HOOK_PROVIDER = BuiltinHookProvider(
    hook_provider="antigravity",
    hook_module="agent_quality.adapters.antigravity",
    hook_extension_key="google.antigravity.hook",
    hook_agent_adapter="antigravity-hooks",
    strategy=AntigravityHookStrategy(),
)

# Compatibility names for the stream adapters used before the contracts split.
CODEX_ADAPTER = CODEX_STREAM_ADAPTER
ANTIGRAVITY_ADAPTER = ANTIGRAVITY_STREAM_ADAPTER

BUILTIN_STREAM_ADAPTERS: tuple[StreamAdapter, ...] = (
    CODEX_STREAM_ADAPTER,
    ANTIGRAVITY_STREAM_ADAPTER,
)
BUILTIN_HOOK_PROVIDERS: tuple[HookProviderAdapter, ...] = (
    CODEX_HOOK_PROVIDER,
    ANTIGRAVITY_HOOK_PROVIDER,
)

STREAM_ADAPTER_CATALOG: Mapping[str, StreamAdapter] = MappingProxyType(
    {
        identity: adapter
        for adapter in BUILTIN_STREAM_ADAPTERS
        for identity in (adapter.name, *adapter.aliases)
    }
)
STREAM_EXECUTABLE_CATALOG: Mapping[str, StreamAdapter] = MappingProxyType(
    {
        executable: adapter
        for adapter in BUILTIN_STREAM_ADAPTERS
        for executable in adapter.executables
    }
)
HOOK_PROVIDER_CATALOG: Mapping[str, HookProviderAdapter] = MappingProxyType(
    {adapter.hook_provider: adapter for adapter in BUILTIN_HOOK_PROVIDERS}
)


def registered_adapters() -> tuple[StreamAdapter, ...]:
    return BUILTIN_STREAM_ADAPTERS


def adapter_named(name: str) -> StreamAdapter:
    try:
        return STREAM_ADAPTER_CATALOG[name]
    except KeyError as exc:
        raise ValueError(f"unknown adapter: {name}") from exc


def hook_provider_names() -> tuple[str, ...]:
    return tuple(HOOK_PROVIDER_CATALOG)


def hook_adapter(provider: str) -> HookProviderAdapter:
    try:
        return HOOK_PROVIDER_CATALOG[provider]
    except KeyError as exc:
        raise ValueError(f"unknown hook provider: {provider}") from exc


def dispatch_hook(provider: str, event: str) -> int:
    return hook_adapter(provider).dispatch_hook(event)


def hook_adapter_for_extensions(
    extensions: Mapping[str, Any],
) -> HookProviderAdapter | None:
    """Resolve a hook only through its exact provider extension identity."""

    for adapter in BUILTIN_HOOK_PROVIDERS:
        if adapter.hook_payload(extensions) is not None:
            return adapter
    return None


def hook_context(
    extensions: Mapping[str, Any],
) -> tuple[HookProviderAdapter, dict[str, Any]] | None:
    adapter = hook_adapter_for_extensions(extensions)
    if adapter is None:
        return None
    payload = adapter.hook_payload(extensions)
    return (adapter, payload) if payload is not None else None


def hook_context_from_source(
    source_payload: Mapping[str, Any],
) -> tuple[HookProviderAdapter, dict[str, Any]] | None:
    extensions = source_payload.get("extensions")
    return hook_context(extensions) if isinstance(extensions, Mapping) else None


def select_agent_adapter(
    command: list[str] | None,
    *,
    prompt: str,
    model: str | None = None,
) -> AdapterSelection:
    if command is not None and not command:
        raise ValueError("agent command cannot be empty")
    if command is None:
        adapter = CODEX_STREAM_ADAPTER
        prepared = adapter.prepare_command(None, prompt=prompt, model=model)
        return AdapterSelection(
            adapter,
            prepared,
            adapter.name,
            adapter.version_executable,
        )

    executable = Path(command[0]).name
    adapter = STREAM_EXECUTABLE_CATALOG.get(executable, CODEX_STREAM_ADAPTER)
    prepared = adapter.prepare_command(command, prompt=prompt, model=model)
    reported_name = adapter.name if executable in STREAM_EXECUTABLE_CATALOG else executable
    return AdapterSelection(adapter, prepared, reported_name, command[0])
