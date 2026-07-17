# Agent Quality

Local-first quality sidecar for coding-agent runs.

This MVP implements the first foundation from `implementation_details/implementation_details.md`:

- `aq run` wraps `codex exec --json`, captures JSONL events, stores artifacts, and runs independent verifiers.
- SQLite separates runs, events, verifier results, artifacts, and human reviews.
- Source payloads are redacted before persistence.
- `aq review`, `aq show`, `aq diff`, `aq trace`, `aq report summary`, and `aq promote` provide the first terminal workflow.
- `aq serve-collector` exposes a loopback HTTP ingestion endpoint for hook adapters.

Install locally:

```bash
python3 -m pip install -e .
```

Or initialize the repository in one step:

```bash
scripts/init_project.sh
```

That creates the virtual environment at `~/venvs/quality-flywheel`, installs `aq` in editable mode, creates `.agent-quality` config files, and runs smoke checks. Pass `--venv PATH` to use a different location. Runtime smoke-test data is stored under `.agent-quality/local`.

Initialize a measured project:

```bash
aq init --repo /path/to/project
```

Install lifecycle hooks once for the current user:

```bash
aq hooks install --provider all
aq hooks status --provider all
```

The installer manages the Agent Quality entries in the user-level provider files:

- Codex: `$CODEX_HOME/hooks.json`, normally `~/.codex/hooks.json`
- Antigravity: `~/.gemini/config/hooks.json`

The selected Python must be able to import the installed `agent_quality` package without a checkout-specific `PYTHONPATH`; installation fails otherwise. `hooks status` validates the managed command and executable path without executing commands read from provider-owned configuration. Codex installation also stops with an error when `config.toml` disables hooks or allows managed hooks only, rather than reporting an inactive setup as configured. Agent Quality can configure Codex hooks but cannot verify Codex's separate trust decision. After installation, open the interactive Codex CLI (not an IDE chat), enter `/hooks`, and trust `~/.codex/hooks.json` (or `$CODEX_HOME/hooks.json`); then start a new IDE chat. Each repository still opts in independently with `aq init`; global hooks require both `.agent-quality/config.yaml` and the git-ignored, path-bound `.agent-quality/local/.initialized` marker. Captured data remains local to the participating repository under `.agent-quality/local`.

The Antigravity integration is passive: it observes completed tool calls and invocation/stop lifecycle events. It intentionally does not install a `PreToolUse` handler, because a telemetry hook must not allow, deny, or force a permission prompt. Antigravity's official hook payloads do not include the user's prompt or the assistant's response text, so these global hooks record session, lifecycle, and completed-tool metadata without fabricating prompt runs. Use Codex hooks or `aq run` when prompt and response capture is required.

For each additional project, only initialization is required:

```bash
cd /path/to/another/project
aq init
```

This release is a hard cutover from the old config-only opt-in. After upgrading, rerun `aq init` once in every existing measured repository so it receives the local consent marker. Rerun it after moving or copying a repository too, because the marker is bound to the repository's canonical path. Global hooks safely skip config-only repositories and path-mismatched copies.

### Remove old project hooks

Releases before the user-level installer wrote checkout-specific hook commands into individual repositories. The global installer does not execute or silently delete those files. Clean each previously configured repository once:

- In `<repo>/.codex/hooks.json`, remove handlers whose command contains `-m agent_quality.cli hook codex`. Delete the file only when it contains no unrelated hooks. Leave `<repo>/.codex/config.toml` intact unless its only purpose was the old Agent Quality `[features] hooks = true` setting.
- In `<repo>/.agents/hooks.json`, remove only the top-level `"agent-quality"` member. Preserve every other member, and delete the file only if the resulting object is empty.

Start a new provider session after cleanup so only the user-level hooks are loaded.

To remove the user-level integrations:

```bash
aq hooks uninstall --provider all
```

Uninstall removes the Agent Quality-managed entries while preserving unrelated provider hooks.

Run a Codex-backed task:

```bash
aq run --repo /path/to/project --verify /path/to/project/.agent-quality/verify.yaml "Fix the parser"
```

The user-level hooks observe documented lifecycle events such as prompt submission, tool use, permission requests, and stop events. The run overview separates the final agent output, tool calls (including MCP inputs/results), and emitted reasoning summaries or commentary. Private chain-of-thought is encrypted by Codex and is not exposed as plaintext. It does not scrape the rendered VS Code sidebar stream. For full streamed rich-client events, build against `codex app-server`; for reproducible MVP evaluation, prefer `aq run`, which wraps `codex exec --json`.

For smoke testing without Codex, pass a command that emits JSONL:

```bash
aq run --allow-dirty "dry run" --agent-command python3 -c 'print("{\"type\":\"message\",\"text\":\"ok\"}")'
```

VS Code extension:

```bash
code vscode-extension
```

Install the Python package first so `aq` is on `PATH`, then launch the extension host from VS Code. The extension adds an Agent Quality activity view and command palette actions for initializing a project, running a measured prompt or selection, installing or checking user-level hooks, starting the collector, and opening run details. **Agent Quality: Install User Hooks** and **Agent Quality: Show User Hook Status** also work when no workspace is open.

When this checkout sits beside `kimi_coding_agent_flywheel`, the initializer also installs the separate `aq-flywheel` analysis worker. Configure `agentQuality.flywheelJudgeCommand` in VS Code as an argument array for a local command that reads a redacted diagnosis prompt from stdin and writes the required diagnosis JSON to stdout. Then run **Agent Quality: Open Flywheel** to select completed runs, launch diagnosis and clustering, and inspect immutable analysis history. The flywheel panel does not run prompt optimization or regression execution.
