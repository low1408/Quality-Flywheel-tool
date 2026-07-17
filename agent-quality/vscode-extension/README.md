# Agent Quality VS Code Extension

The extension includes separate run and flywheel webviews. To enable flywheel analysis, install the sibling `kimi_coding_agent_flywheel` package so `aq-flywheel` is available and configure `agentQuality.flywheelJudgeCommand` as a command argument array. The judge receives an egress-redacted prompt on stdin and must return the `LLMJudgeDiagnoser` JSON object on stdout.

This extension provides a thin VS Code interface over the `aq` CLI.

## Features

- Initialize `.agent-quality` in the current workspace.
- Run a measured Codex task from an input prompt or selected editor text.
- Install and inspect user-level Codex and Antigravity hooks.
- Start and stop the local loopback collector.
- View summary counts and recent runs in the Agent Quality activity view.
- Open run details, diffs, traces, and terminal reviews from the run tree.

## Hook setup

Run **Agent Quality: Install User Hooks** once. It invokes:

```bash
aq hooks install --provider all
```

The command works without an open workspace and manages hooks in `$CODEX_HOME/hooks.json` (normally `~/.codex/hooks.json`) and `~/.gemini/config/hooks.json`. To trust the Codex definitions, open an integrated terminal, run the interactive `codex` CLI, then use `/hooks`. Start a new IDE chat after trusting them. **Agent Quality: Show User Hook Status** runs `aq hooks status --provider all` but cannot verify Codex's trust decision.

Existing extension installations already expose **Agent Quality: Open Dashboard**. Update to extension version 0.1.14 or later only to use the new user-hook install and status commands; the same setup is always available through the `aq hooks` CLI.

Each repository opts in separately. Open it and run **Agent Quality: Initialize Project**, or use:

```bash
cd /path/to/project
aq init
```

Global hooks require both `.agent-quality/config.yaml` and the git-ignored, path-bound `.agent-quality/local/.initialized` marker created by `aq init`. Rerun `aq init` once in projects initialized by an older release, and after moving or copying a project. Participating repositories keep their captured data under `.agent-quality/local`; the global install and status commands do not redirect data to the currently open workspace.

Codex hooks record submitted prompt text. Antigravity's documented installed-hook payloads expose lifecycle and completed-tool metadata but not the submitted prompt or assistant response, so its integration does not fabricate prompt runs.

Remove the user-level integrations with:

```bash
aq hooks uninstall --provider all
```

## Development

Install the Python package first so the `aq` command is available:

```bash
python3 -m pip install -e ..
```

Then open this folder in VS Code and run the extension host.

By default, extension commands set `AGENT_QUALITY_HOME` to:

```text
<workspace>/.agent-quality/local
```

This project-local setting applies to run, collector, report, and dashboard operations. User hook install and status commands intentionally do not set it, and globally installed provider hooks ignore `AGENT_QUALITY_HOME` so it cannot bypass per-project initialization.

Override `agentQuality.aqCommand` if `aq` is not on `PATH`.

If commands do not run from VS Code but `aq` works in your terminal, set:

```json
{
  "agentQuality.aqCommand": "python3 -m agent_quality.cli",
  "agentQuality.cliSourceRoot": "/home/harry/Documents/Github-Projects/personal-projects/quality-flywheel/agent-quality"
}
```

The source root setting adds `<sourceRoot>/src` to `PYTHONPATH`, which avoids VS Code desktop PATH differences.

By default, the user-level hook installer embeds the Python interpreter running `aq`. Set `agentQuality.pythonPath` only when you need to override that interpreter.
