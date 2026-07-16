# Repository Guidelines

## Project Structure & Module Organization

This repository contains two related Python projects and a VS Code extension.

- `agent-quality/` is the local-first telemetry sidecar. Source lives in `agent-quality/src/agent_quality/`, tests in `agent-quality/tests/`, static dashboard assets in `agent-quality/src/agent_quality/collector/static/`, and setup helpers in `agent-quality/scripts/`.
- `kimi_coding_agent_flywheel/` is the failure diagnosis and clustering worker. Its package modules are organized by responsibility (`core/`, `clustering/`, `optimization/`, `regression/`, `monitoring/`), with tests in `kimi_coding_agent_flywheel/tests/`.
- `agent-quality/vscode-extension/` contains the extension entry point in `src/extension.js` and webview/media assets in `media/`.

## Build, Test, and Development Commands

Use the project virtualenv at `~/venvs/quality-flywheel/` when running Python commands. For example:

- `~/venvs/quality-flywheel/bin/python -m pytest` runs tests with the repository's expected interpreter.
- `~/venvs/quality-flywheel/bin/python -m pip install -e .` installs a package into that virtualenv.

From `agent-quality/`:

- `~/venvs/quality-flywheel/bin/python -m pip install -e .` installs the `aq` CLI in editable mode.
- `scripts/init_project.sh` creates the expected local virtualenv and smoke-checks the project.
- `~/venvs/quality-flywheel/bin/python -m pytest` runs the Agent Quality test suite.

From `kimi_coding_agent_flywheel/`:

- `~/venvs/quality-flywheel/bin/python -m pip install -e .` installs the `aq-flywheel` CLI in editable mode.
- `~/venvs/quality-flywheel/bin/python -m pytest` runs the flywheel tests.

From `agent-quality/vscode-extension/`:

- `npm run check` syntax-checks `src/extension.js`.
- `npm run validate-package` verifies `package.json` is valid JSON.

## Coding Style & Naming Conventions

Use Python 3.11+ and keep modules small, typed where helpful, and organized around the existing package boundaries. Python tests use `test_*.py` files and descriptive `test_*` function or method names. JavaScript is plain CommonJS for the extension; keep command IDs under the existing `agentQuality.*` namespace. Prefer clear snake_case for Python functions and modules, PascalCase for classes, and camelCase for JavaScript variables.

## Testing Guidelines

Add focused tests next to the package you change. Use pytest for `agent-quality`; the flywheel tests currently mix pytest discovery with `unittest.TestCase`. Prefer temporary paths and monkeypatching over writing real user state. For UI asset changes, update or add tests under `agent-quality/tests/` and run the extension syntax checks.

## Commit & Pull Request Guidelines

Recent commits use short imperative or descriptive messages, for example `fixed vsx extension outdated error` and `before refactoring dashboard`. Keep new commits concise and scoped. Pull requests should describe the behavior change, list tests run, call out affected CLIs or VS Code commands, and include screenshots for dashboard or webview changes.

## Security & Configuration Tips

Do not commit runtime data from `.agent-quality/local`, local virtualenvs, or captured agent payloads. Keep redaction behavior intact when changing ingestion, storage, review, or dashboard code.
