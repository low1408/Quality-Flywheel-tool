from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import pytest

import agent_quality.hook_installation as hook_installation
from agent_quality.adapters.hook_runtime import CONSENT_MARKER_NAME
from agent_quality.cli import _init_project, main
from agent_quality.hook_installation import (
    HookConfigError,
    hook_path,
    hooks_status,
    install_hooks,
    uninstall_hooks,
)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _commands(value: object) -> list[str]:
    commands: list[str] = []
    if isinstance(value, dict):
        command = value.get("command")
        if value.get("type") == "command" and isinstance(command, str):
            commands.append(command)
        for child in value.values():
            commands.extend(_commands(child))
    elif isinstance(value, list):
        for child in value:
            commands.extend(_commands(child))
    return commands


def _command_handlers(value: object) -> list[dict]:
    handlers: list[dict] = []
    if isinstance(value, dict):
        if value.get("type") == "command" and isinstance(value.get("command"), str):
            handlers.append(value)
        for child in value.values():
            handlers.extend(_command_handlers(child))
    elif isinstance(value, list):
        for child in value:
            handlers.extend(_command_handlers(child))
    return handlers


def _python_wrapper(path: Path) -> Path:
    if os.name == "nt":
        pytest.skip("the disposable Python launcher uses a POSIX shell")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_global_hook_paths_honor_codex_home(tmp_path):
    user_home = tmp_path / "user"
    codex_home = tmp_path / "custom-codex"
    environment = {"CODEX_HOME": str(codex_home)}

    assert hook_path("codex", home=user_home, environ=environment) == codex_home / "hooks.json"
    assert hook_path("antigravity", home=user_home, environ=environment) == (
        user_home / ".gemini" / "config" / "hooks.json"
    )


def test_install_all_merges_unrelated_hooks_and_uses_absolute_python(tmp_path):
    user_home = tmp_path / "user"
    codex_path = user_home / ".codex" / "hooks.json"
    antigravity_path = user_home / ".gemini" / "config" / "hooks.json"
    codex_path.parent.mkdir(parents=True)
    antigravity_path.parent.mkdir(parents=True)
    codex_path.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {"type": "command", "command": "echo third-party"},
                                {
                                    "type": "command",
                                    "command": (
                                        "AGENT_QUALITY_HOME=/old /old/python -m "
                                        "agent_quality.cli hook codex PostToolUse"
                                    ),
                                },
                            ],
                        }
                    ],
                    "CustomEvent": [
                        {"hooks": [{"type": "command", "command": "echo custom"}]}
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    antigravity_path.write_text(
        json.dumps(
            {
                "existing-third-party": {
                    "PreToolUse": [
                        {"hooks": [{"type": "command", "command": "echo antigravity"}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    results = install_hooks("all", sys.executable, home=user_home, environ={})

    assert [result.provider for result in results] == ["codex", "antigravity"]
    assert all(result.installed and result.changed for result in results)
    codex = _json(codex_path)
    antigravity = _json(antigravity_path)
    assert codex["version"] == 1
    assert codex["hooks"]["CustomEvent"][0]["hooks"][0]["command"] == "echo custom"
    assert "echo third-party" in _commands(codex["hooks"]["PostToolUse"])
    assert antigravity["existing-third-party"]["PreToolUse"][0]["hooks"][0]["command"] == (
        "echo antigravity"
    )
    integration = antigravity["agent-quality"]
    assert integration["enabled"] is True
    assert "PreToolUse" not in integration
    assert set(integration) == {
        "enabled",
        "PostToolUse",
        "PreInvocation",
        "PostInvocation",
        "Stop",
    }
    assert integration["PostToolUse"][0]["matcher"] == "*"
    assert integration["PostToolUse"][0]["hooks"][0]["type"] == "command"
    for event in ("PreInvocation", "PostInvocation", "Stop"):
        assert integration[event][0]["type"] == "command"
        assert "hooks" not in integration[event][0]

    commands = _commands(codex["hooks"]) + _commands(antigravity["agent-quality"])
    assert commands
    agent_quality_commands = [command for command in commands if "agent_quality.cli" in command]
    assert all(command.startswith(str(Path(sys.executable).absolute())) for command in agent_quality_commands)
    assert all("AGENT_QUALITY_HOME" not in command for command in agent_quality_commands)
    assert all("PYTHONPATH" not in command for command in agent_quality_commands)
    for handler in _command_handlers(codex["hooks"]):
        if "agent_quality.cli" not in handler["command"]:
            continue
        assert handler["commandWindows"] == subprocess.list2cmdline(
            shlex.split(handler["command"])
        )
    assert all(
        "commandWindows" not in handler
        for handler in _command_handlers(antigravity["agent-quality"])
    )
    assert not (user_home / ".codex" / "config.toml").exists()


def test_install_is_idempotent_and_status_reports_each_provider(tmp_path):
    first = install_hooks("all", home=tmp_path, environ={})
    paths = [result.path for result in first]
    before = [path.read_bytes() for path in paths]

    second = install_hooks("all", home=tmp_path, environ={})

    assert not any(result.changed for result in second)
    assert [path.read_bytes() for path in paths] == before
    status = hooks_status("all", home=tmp_path, environ={})
    assert [result.installed for result in status] == [True, True]


def test_uninstall_is_surgical_and_idempotent(tmp_path):
    codex_path = tmp_path / ".codex" / "hooks.json"
    antigravity_path = tmp_path / ".gemini" / "config" / "hooks.json"
    install_hooks("all", home=tmp_path, environ={})

    codex = _json(codex_path)
    codex["hooks"]["PostToolUse"][0]["hooks"].append(
        {"type": "command", "command": "echo keep-codex"}
    )
    codex_path.write_text(json.dumps(codex), encoding="utf-8")
    antigravity = _json(antigravity_path)
    antigravity["third-party"] = {"command": "echo keep-antigravity"}
    antigravity_path.write_text(json.dumps(antigravity), encoding="utf-8")

    results = uninstall_hooks("all", home=tmp_path, environ={})

    assert all(result.changed and not result.installed for result in results)
    assert "echo keep-codex" in _commands(_json(codex_path))
    assert _json(antigravity_path)["third-party"] == {
        "command": "echo keep-antigravity"
    }
    assert "agent-quality" not in _json(antigravity_path)
    assert [result.installed for result in hooks_status("all", home=tmp_path, environ={})] == [
        False,
        False,
    ]
    assert not any(
        result.changed for result in uninstall_hooks("all", home=tmp_path, environ={})
    )


def test_malformed_json_fails_without_modifying_any_provider(tmp_path):
    antigravity_path = tmp_path / ".gemini" / "config" / "hooks.json"
    antigravity_path.parent.mkdir(parents=True)
    malformed = "{ definitely not json\n"
    antigravity_path.write_text(malformed, encoding="utf-8")

    with pytest.raises(HookConfigError, match="cannot read valid JSON"):
        install_hooks("all", home=tmp_path, environ={})

    assert antigravity_path.read_text(encoding="utf-8") == malformed
    assert not (tmp_path / ".codex" / "hooks.json").exists()


def test_install_preserves_existing_file_permissions_and_leaves_no_temp_files(tmp_path):
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text("{}\n", encoding="utf-8")
    hooks_path.chmod(0o640)

    install_hooks("codex", home=tmp_path, environ={})

    assert hooks_path.stat().st_mode & 0o777 == 0o640
    assert not list(hooks_path.parent.glob(".hooks.json.*.tmp"))


def test_install_and_uninstall_preserve_a_symlinked_hook_file(tmp_path):
    hooks_path = tmp_path / ".codex" / "hooks.json"
    target = tmp_path / "dotfiles" / "codex-hooks.json"
    hooks_path.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps({"hooks": {"CustomEvent": [{"hooks": [{"command": "keep"}]}]}}),
        encoding="utf-8",
    )
    hooks_path.symlink_to(target)

    install_hooks("codex", home=tmp_path, environ={})

    assert hooks_path.is_symlink()
    assert "UserPromptSubmit" in _json(target)["hooks"]

    uninstall_hooks("codex", home=tmp_path, environ={})

    assert hooks_path.is_symlink()
    assert _json(target) == {
        "hooks": {"CustomEvent": [{"hooks": [{"command": "keep"}]}]}
    }


def test_selected_python_path_with_spaces_is_quoted(tmp_path):
    python = _python_wrapper(tmp_path / "environment with spaces" / "python")

    install_hooks("codex", str(python), home=tmp_path, environ={})

    handlers = _command_handlers(_json(tmp_path / ".codex" / "hooks.json"))
    assert all(
        handler["command"].startswith(f"'{python}' -m agent_quality.cli")
        for handler in handlers
    )
    assert all(
        handler["commandWindows"].startswith(f'"{python}" -m agent_quality.cli')
        for handler in handlers
    )
    assert hooks_status("codex", home=tmp_path, environ={})[0].installed


def test_antigravity_uses_native_windows_quoting(tmp_path, monkeypatch):
    python = _python_wrapper(tmp_path / "windows environment with spaces" / "python.exe")
    monkeypatch.setattr(hook_installation, "_IS_WINDOWS", True)

    install_hooks("antigravity", str(python), home=tmp_path, environ={})

    integration = _json(tmp_path / ".gemini" / "config" / "hooks.json")["agent-quality"]
    handlers = _command_handlers(integration)
    assert handlers
    assert all(handler["command"].startswith(f'"{python}" -m ') for handler in handlers)
    assert hooks_status("antigravity", home=tmp_path, environ={})[0].installed


def test_antigravity_windows_status_preserves_unquoted_backslashes(tmp_path, monkeypatch):
    python = _python_wrapper(tmp_path / r"C:\AQ\python.exe")
    monkeypatch.setattr(hook_installation, "_IS_WINDOWS", True)

    install_hooks("antigravity", str(python), home=tmp_path, environ={})

    integration = _json(tmp_path / ".gemini" / "config" / "hooks.json")["agent-quality"]
    command = integration["PostToolUse"][0]["hooks"][0]["command"]
    assert r"C:\AQ\python.exe" in command
    assert hooks_status("antigravity", home=tmp_path, environ={})[0].installed


def test_antigravity_windows_command_quotes_a_drive_path(monkeypatch):
    python = PureWindowsPath(r"C:\Program Files\Agent Quality\python.exe")
    monkeypatch.setattr(hook_installation, "_IS_WINDOWS", True)

    command = hook_installation._hook_command(python, "antigravity", "PostToolUse")

    assert command == subprocess.list2cmdline(
        [str(python), "-m", "agent_quality.cli", "hook", "antigravity", "PostToolUse"]
    )
    assert command.startswith(r'"C:\Program Files\Agent Quality\python.exe"')


def test_status_detects_a_deleted_python_environment(tmp_path):
    python = _python_wrapper(tmp_path / "disposable-environment" / "python")
    install_hooks("codex", str(python), home=tmp_path, environ={})
    assert hooks_status("codex", home=tmp_path, environ={})[0].installed

    python.unlink()

    assert not hooks_status("codex", home=tmp_path, environ={})[0].installed


def test_status_detects_an_environment_that_can_no_longer_import_the_package(tmp_path):
    python = _python_wrapper(tmp_path / "broken-environment" / "python")
    install_hooks("codex", str(python), home=tmp_path, environ={})
    assert hooks_status("codex", home=tmp_path, environ={})[0].installed

    python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")

    assert not hooks_status("codex", home=tmp_path, environ={})[0].installed


def test_install_rejects_an_executable_that_cannot_import_agent_quality(tmp_path):
    executable = tmp_path / "not-python"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)

    with pytest.raises(HookConfigError, match="cannot import agent_quality.cli"):
        install_hooks("codex", str(executable), home=tmp_path, environ={})

    assert not (tmp_path / ".codex" / "hooks.json").exists()


def test_status_rejects_narrow_matchers_and_wrong_antigravity_shapes(tmp_path):
    install_hooks("all", home=tmp_path, environ={})
    codex_path = tmp_path / ".codex" / "hooks.json"
    antigravity_path = tmp_path / ".gemini" / "config" / "hooks.json"

    codex = _json(codex_path)
    codex["hooks"]["PostToolUse"][-1]["matcher"] = "^Bash$"
    codex_path.write_text(json.dumps(codex), encoding="utf-8")
    assert not hooks_status("codex", home=tmp_path, environ={})[0].installed

    install_hooks("codex", home=tmp_path, environ={})
    codex = _json(codex_path)
    del codex["hooks"]["Stop"][-1]["hooks"][0]["commandWindows"]
    codex_path.write_text(json.dumps(codex), encoding="utf-8")
    assert not hooks_status("codex", home=tmp_path, environ={})[0].installed

    antigravity = _json(antigravity_path)
    handler = antigravity["agent-quality"]["PreInvocation"][0]
    antigravity["agent-quality"]["PreInvocation"] = [{"hooks": [handler]}]
    antigravity_path.write_text(json.dumps(antigravity), encoding="utf-8")
    assert not hooks_status("antigravity", home=tmp_path, environ={})[0].installed

    install_hooks("antigravity", home=tmp_path, environ={})
    antigravity = _json(antigravity_path)
    antigravity["agent-quality"]["PostToolUse"][0]["matcher"] = "^run_command$"
    antigravity_path.write_text(json.dumps(antigravity), encoding="utf-8")
    assert not hooks_status("antigravity", home=tmp_path, environ={})[0].installed

    install_hooks("antigravity", home=tmp_path, environ={})
    antigravity = _json(antigravity_path)
    antigravity["agent-quality"]["PreToolUse"] = [
        antigravity["agent-quality"]["PostToolUse"][0]
    ]
    antigravity_path.write_text(json.dumps(antigravity), encoding="utf-8")
    assert not hooks_status("antigravity", home=tmp_path, environ={})[0].installed


def test_status_honors_provider_disable_flags_and_reinstall_enables_antigravity(tmp_path):
    install_hooks("all", home=tmp_path, environ={})
    codex_config = tmp_path / ".codex" / "config.toml"
    antigravity_path = tmp_path / ".gemini" / "config" / "hooks.json"

    codex_config.write_text("[features]\nhooks = false\n", encoding="utf-8")
    antigravity = _json(antigravity_path)
    antigravity["agent-quality"]["enabled"] = False
    antigravity_path.write_text(json.dumps(antigravity), encoding="utf-8")

    assert not hooks_status("codex", home=tmp_path, environ={})[0].installed
    assert not hooks_status("antigravity", home=tmp_path, environ={})[0].installed

    codex_config.write_text("allow_managed_hooks_only = true\n", encoding="utf-8")
    assert not hooks_status("codex", home=tmp_path, environ={})[0].installed

    install_hooks("antigravity", home=tmp_path, environ={})
    assert _json(antigravity_path)["agent-quality"]["enabled"] is True
    assert hooks_status("antigravity", home=tmp_path, environ={})[0].installed


@pytest.mark.parametrize(
    "config",
    [
        "[features]\nhooks = false\n",
        "allow_managed_hooks_only = true\n[features]\nhooks = true\n",
    ],
)
def test_codex_install_refuses_an_explicitly_disabled_user_hook_layer(tmp_path, config):
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    hooks_path = codex_dir / "hooks.json"
    existing = '{"hooks": {"CustomEvent": []}}\n'
    hooks_path.write_text(existing, encoding="utf-8")
    (codex_dir / "config.toml").write_text(config, encoding="utf-8")

    with pytest.raises(HookConfigError, match="Codex user hooks are disabled"):
        install_hooks("codex", home=tmp_path, environ={})

    assert hooks_path.read_text(encoding="utf-8") == existing


def test_cli_defaults_to_all_providers_and_legacy_commands_are_removed(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)

    main(["hooks", "install"])
    output = capsys.readouterr().out

    assert "codex: installed" in output
    assert "antigravity: installed" in output
    assert "codex: trust is not verified by aq" in output
    assert "in a terminal, cd to the repository and run codex" in output
    assert "use /hooks to trust" in output
    assert "afterward start a new IDE chat" in output
    assert str(tmp_path / ".codex" / "hooks.json") in output
    assert (tmp_path / ".codex" / "hooks.json").exists()
    assert (tmp_path / ".gemini" / "config" / "hooks.json").exists()
    with pytest.raises(SystemExit) as exc:
        main(["install-codex-hooks"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        main(["install-antigravity-hooks"])
    assert exc.value.code == 2

    main(["hooks", "status", "--provider", "codex"])
    status_output = capsys.readouterr().out
    assert "codex: installed" in status_output
    assert "codex: trust is not verified by aq" in status_output


def test_init_adds_local_runtime_gitignore_without_overwriting_existing_entries(tmp_path):
    repo = tmp_path / "repo"
    aq = repo / ".agent-quality"
    aq.mkdir(parents=True)
    (repo / ".git").mkdir()
    (aq / ".gitignore").write_text("custom-cache/", encoding="utf-8")

    _init_project(repo)
    _init_project(repo)

    assert (aq / ".gitignore").read_text(encoding="utf-8") == "custom-cache/\nlocal/\n"
    marker = aq / "local" / CONSENT_MARKER_NAME
    assert marker.read_text(encoding="utf-8") == f"{repo.resolve()}\n"


def test_init_rejects_symlinked_managed_file_before_writing(tmp_path):
    repo = tmp_path / "repo"
    aq = repo / ".agent-quality"
    aq.mkdir(parents=True)
    (repo / ".git").mkdir()
    external = tmp_path / "external-gitignore"
    external.write_text("keep-me\n", encoding="utf-8")
    (aq / ".gitignore").symlink_to(external)

    with pytest.raises(SystemExit, match="unsafe Agent Quality file"):
        _init_project(repo)

    assert external.read_text(encoding="utf-8") == "keep-me\n"
    assert not (aq / "cases").exists()
    assert not (aq / "config.yaml").exists()


def test_init_rejects_config_directory_before_writing(tmp_path):
    repo = tmp_path / "repo"
    aq = repo / ".agent-quality"
    (aq / "config.yaml").mkdir(parents=True)
    (repo / ".git").mkdir()

    with pytest.raises(SystemExit, match="unsafe Agent Quality file"):
        _init_project(repo)

    assert not (aq / "cases").exists()
    assert not (aq / "verify.yaml").exists()
