# Quality Flywheel

Quality Flywheel is a local-first toolkit for observing coding-agent runs and
turning their failures into reviewable, clustered evidence. The repository is
a small monorepo with three deliverables:

| Component | Purpose | Entry point |
| --- | --- | --- |
| [Agent Quality](agent-quality/README.md) | Capture, redaction, verification, review, and the local collector | `aq` |
| [Flywheel worker](kimi_coding_agent_flywheel/README.md) | Offline diagnosis and clustering over Agent Quality data | `aq-flywheel` |
| [VS Code extension](agent-quality/vscode-extension/README.md) | Editor UI backed by the Agent Quality CLI and collector | Extension commands |

The supported worker surface is diagnosis and clustering. Prompt optimization,
benchmark execution, regression execution, and automatic prompt promotion are
not production features of this release.

## Development

Python 3.11 or newer and Node.js are required. Repository commands default to
the project virtual environment at `~/venvs/quality-flywheel`; override it with
`QUALITY_FLYWHEEL_VENV=/path/to/venv` or `PYTHON=/path/to/python`.

```bash
make install-dev
make check
```

`make check` is the single local and CI verification command. It runs both
Python test suites and validates the VS Code extension source and manifest.
Individual targets are available through `make help`.

Runtime telemetry and SQLite databases live below `.agent-quality/local/` and
are intentionally ignored. `make clean` removes only generated Python caches,
egg metadata, build directories, packaged `.vsix` files, and the extension's
generated dashboard copies. Git metadata and every `.agent-quality/local/` tree
are explicitly excluded and preserved. For
direct CLI and collector use, set `AGENT_QUALITY_HOME` to keep the database and
artifacts in another directory; globally installed provider hooks intentionally
continue to use each initialized repository's local data directory.

## Repository layout

```text
agent-quality/
  src/agent_quality/             telemetry and verification package
  tests/                         Agent Quality tests
  vscode-extension/              VS Code client
kimi_coding_agent_flywheel/
  src/kimi_coding_agent_flywheel/ diagnosis and clustering worker
  tests/                         worker tests
scripts/                         repository-wide development helpers
```

Both Python distributions use `src/` layouts so imports during tests and local
development resolve installed package code consistently.

## Security and privacy

Agent Quality is designed for local operation. Treat `.agent-quality/local/` as
sensitive: it can contain prompts, tool activity, review data, and artifacts.
Do not commit or publish it. Review the collector host and token settings before
binding beyond loopback.

## License

Released under the [MIT License](LICENSE).
