from __future__ import annotations

import pytest

import agent_quality.cli as cli
from agent_quality.adapters.registry import (
    ANTIGRAVITY_ADAPTER,
    ANTIGRAVITY_HOOK_PROVIDER,
    BUILTIN_HOOK_PROVIDERS,
    BUILTIN_STREAM_ADAPTERS,
    CODEX_ADAPTER,
    CODEX_HOOK_PROVIDER,
    HOOK_PROVIDER_CATALOG,
    STREAM_ADAPTER_CATALOG,
    HookProviderAdapter,
    StreamAdapter,
    adapter_named,
    hook_adapter,
    hook_adapter_for_extensions,
    hook_context,
    hook_context_from_source,
    hook_provider_names,
    registered_adapters,
    select_agent_adapter,
)
from agent_quality.hook_installation import PROVIDERS


def test_builtin_adapters_have_one_central_identity_and_hook_registry():
    assert registered_adapters() == (CODEX_ADAPTER, ANTIGRAVITY_ADAPTER)
    assert adapter_named("codex") is CODEX_ADAPTER
    assert adapter_named("agy") is ANTIGRAVITY_ADAPTER
    assert hook_provider_names() == ("codex", "antigravity")
    assert PROVIDERS == hook_provider_names()
    assert hook_adapter("codex") is CODEX_HOOK_PROVIDER
    assert hook_adapter("antigravity") is ANTIGRAVITY_HOOK_PROVIDER
    assert hook_adapter("codex").hook_events == (
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PermissionRequest",
        "Stop",
    )


def test_default_and_alias_commands_are_prepared_by_the_selected_adapter():
    default = select_agent_adapter(None, prompt="fix it", model="gpt-test")
    assert default.adapter is CODEX_ADAPTER
    assert default.reported_name == "codex-cli"
    assert default.command == [
        "codex",
        "exec",
        "--json",
        "--model",
        "gpt-test",
        "--sandbox",
        "workspace-write",
        "fix it",
    ]

    antigravity = select_agent_adapter(["/opt/bin/agy"], prompt="fix it")
    assert antigravity.adapter is ANTIGRAVITY_ADAPTER
    assert antigravity.reported_name == "antigravity"
    assert antigravity.command == [
        "/opt/bin/agy",
        "-p",
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
        "fix it",
    ]
    assert antigravity.version_executable == "/opt/bin/agy"


def test_unknown_command_keeps_legacy_codex_stream_parser_and_reports_executable():
    selection = select_agent_adapter(
        ["/usr/local/bin/custom-agent", "--json"],
        prompt="unused",
    )

    assert selection.adapter is CODEX_ADAPTER
    assert selection.command == ["/usr/local/bin/custom-agent", "--json"]
    assert selection.reported_name == "custom-agent"
    assert selection.version_executable == "/usr/local/bin/custom-agent"


def test_hook_context_routes_by_registered_extension_key():
    extensions = {"google.antigravity.hook": {"conversationId": "conversation-1"}}
    context = hook_context(extensions)

    assert context is not None
    adapter, payload = context
    assert adapter is ANTIGRAVITY_HOOK_PROVIDER
    assert hook_adapter_for_extensions(extensions) is ANTIGRAVITY_HOOK_PROVIDER
    assert payload == {"conversationId": "conversation-1"}


def test_unknown_hook_extensions_have_a_graceful_fallback():
    extensions = {"third.party.hook": {"event": "completed"}}

    assert hook_adapter_for_extensions(extensions) is None
    assert hook_context(extensions) is None


def test_source_provider_without_exact_hook_extension_is_not_routed():
    source = {
        "source": {"provider": "openai", "product": "codex"},
        "data": {"cwd": "/tmp", "last_assistant_message": "not a hook"},
    }

    assert hook_context_from_source(source) is None


def test_builtin_catalogs_are_separate_complete_and_immutable():
    assert BUILTIN_STREAM_ADAPTERS == (CODEX_ADAPTER, ANTIGRAVITY_ADAPTER)
    assert BUILTIN_HOOK_PROVIDERS == (
        CODEX_HOOK_PROVIDER,
        ANTIGRAVITY_HOOK_PROVIDER,
    )
    assert all(isinstance(adapter, StreamAdapter) for adapter in BUILTIN_STREAM_ADAPTERS)
    assert all(
        isinstance(provider, HookProviderAdapter)
        for provider in BUILTIN_HOOK_PROVIDERS
    )

    with pytest.raises(TypeError):
        STREAM_ADAPTER_CATALOG["demo"] = CODEX_ADAPTER  # type: ignore[index]
    with pytest.raises(TypeError):
        HOOK_PROVIDER_CATALOG["demo"] = CODEX_HOOK_PROVIDER  # type: ignore[index]


def test_cli_hook_dispatches_through_registry_without_provider_branches(monkeypatch):
    calls: list[tuple[str, str]] = []

    def dispatch(provider: str, event: str) -> int:
        calls.append((provider, event))
        return 17

    monkeypatch.setattr(cli, "dispatch_hook", dispatch)

    with pytest.raises(SystemExit, match="17"):
        cli.main(["hook", "antigravity", "Stop"])

    assert calls == [("antigravity", "Stop")]


def test_unknown_adapter_name_has_actionable_error():
    with pytest.raises(ValueError, match="unknown adapter: missing"):
        adapter_named("missing")

    with pytest.raises(ValueError, match="unknown hook provider: missing"):
        hook_adapter("missing")
