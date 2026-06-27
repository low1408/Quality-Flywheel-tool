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

That creates a local virtual environment, installs `aq` in editable mode, creates `.agent-quality` config files, and runs smoke checks. Runtime smoke-test data is stored under `.agent-quality/local`.

Initialize a measured project:

```bash
aq init --repo /path/to/project
```

Run a Codex-backed task:

```bash
aq run --repo /path/to/project --verify /path/to/project/.agent-quality/verify.yaml "Fix the parser"
```

Observe normal Codex IDE/CLI usage with hooks:

```bash
aq install-codex-hooks --repo /path/to/project
```

Then restart the Codex IDE extension or start a new Codex session. Codex will ask you to review/trust the project-local hooks before they run. Hook-originated events are stored in the same SQLite database under `.agent-quality/local` when you use the initializer defaults.

This observes documented lifecycle events such as prompt submission, tool use, permission requests, and stop events. The run overview separates the final agent output, tool calls (including MCP inputs/results), and emitted reasoning summaries or commentary. Private chain-of-thought is encrypted by Codex and is not exposed as plaintext. It does not scrape the rendered VS Code sidebar stream. For full streamed rich-client events, build against `codex app-server`; for reproducible MVP evaluation, prefer `aq run`, which wraps `codex exec --json`.

For smoke testing without Codex, pass a command that emits JSONL:

```bash
aq run --allow-dirty "dry run" --agent-command python3 -c 'print("{\"type\":\"message\",\"text\":\"ok\"}")'
```

VS Code extension:

```bash
code vscode-extension
```

Install the Python package first so `aq` is on `PATH`, then launch the extension host from VS Code. The extension adds an Agent Quality activity view and command palette actions for initializing a project, running a measured prompt or selection, installing Codex hooks, starting the collector, and opening run details.
