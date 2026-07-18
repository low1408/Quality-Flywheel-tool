# Agent Quality VS Code Extension

The extension includes separate run and flywheel webviews. To enable flywheel analysis, install the sibling `kimi_coding_agent_flywheel` package so `aq-flywheel` is available and configure `agentQuality.flywheelJudgeCommand` as a command argument array. The judge receives an egress-redacted prompt on stdin and must return the `LLMJudgeDiagnoser` JSON object on stdout.

This extension provides a thin VS Code interface over the `aq` CLI.

## Features

- Initialize `.agent-quality` at the current Git repository root.
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

Extension 0.1.15 requires Agent Quality 0.2.0 or later. It uses the CLI's
versioned, allowlisted `ui-api` contract for dashboard data and keeps database
access in the Python collector package. The same hook setup remains available
through the `aq hooks` CLI. Dashboard and flywheel requests preflight the
configured `aq` executable and show a configuration error when it is too old.

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

The browser dashboard assets under `agent_quality/collector/static` are the
single source of truth. Generate the extension's ignored packaging copies and
check every JavaScript module before launching or packaging:

```bash
npm run check
```

`vscode:prepublish` also performs this synchronization automatically.

By default, extension commands set `AGENT_QUALITY_HOME` to:

```text
<git-repository-root>/.agent-quality/local
```

This repository-local setting applies to run, collector, report, and dashboard operations. User hook install and status commands intentionally do not set it, and globally installed provider hooks ignore `AGENT_QUALITY_HOME` so it cannot bypass per-project initialization.

In a multi-root workspace, the Runs view groups results by Git repository.
Workspace folders that resolve to the same repository are shown only once.

Setting `agentQuality.collectorToken` passes the token to `aq serve-collector`.
All collector API routes then require bearer authentication; the standalone
browser dashboard prompts for the token and stores it only in session storage.

Override `agentQuality.aqCommand` if `aq` is not on `PATH`.

If commands do not run from VS Code but `aq` works in your terminal, set:

```json
{
  "agentQuality.aqCommand": "python3 -m agent_quality.cli",
  "agentQuality.cliSourceRoot": "/path/to/quality-flywheel/agent-quality"
}
```

The source root setting adds `<sourceRoot>/src` to `PYTHONPATH`, which avoids VS Code desktop PATH differences.

By default, the user-level hook installer embeds the Python interpreter running `aq`. Set `agentQuality.pythonPath` only when you need to override that interpreter.
