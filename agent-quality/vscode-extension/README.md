# Agent Quality VS Code Extension

The extension includes separate run and flywheel webviews. To enable flywheel analysis, install the sibling `kimi_coding_agent_flywheel` package so `aq-flywheel` is available and configure `agentQuality.flywheelJudgeCommand` as a command argument array. The judge receives an egress-redacted prompt on stdin and must return the `LLMJudgeDiagnoser` JSON object on stdout.

This extension provides a thin VS Code interface over the `aq` CLI.

## Features

- Initialize `.agent-quality` in the current workspace.
- Run a measured Codex task from an input prompt or selected editor text.
- Install Codex lifecycle hooks for everyday usage capture.
- Start and stop the local loopback collector.
- View summary counts and recent runs in the Agent Quality activity view.
- Open run details, diffs, traces, and terminal reviews from the run tree.

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

Override `agentQuality.aqCommand` if `aq` is not on `PATH`.

If commands do not run from VS Code but `aq` works in your terminal, set:

```json
{
  "agentQuality.aqCommand": "python3 -m agent_quality.cli",
  "agentQuality.cliSourceRoot": "/home/harry/Documents/Github-Projects/personal-projects/quality-flywheel/agent-quality"
}
```

The source root setting adds `<sourceRoot>/src` to `PYTHONPATH`, which avoids VS Code desktop PATH differences.
