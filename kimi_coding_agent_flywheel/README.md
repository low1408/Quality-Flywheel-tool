# Kimi Coding Agent Flywheel

`kimi-coding-agent-flywheel` is the failure-diagnosis and clustering worker for
Agent Quality. Its supported command-line interface analyzes existing Agent
Quality runs with an explicitly configured judge command and persists an
immutable analysis history.

## Install

From this directory, install the worker and its `aq-flywheel` command:

```bash
python -m pip install -e .
```

## Analyze runs

The judge command reads a redacted diagnosis prompt from standard input and
must write the documented JSON diagnosis object to standard output.

```bash
aq-flywheel analyze \
  --db /path/to/quality.sqlite3 \
  --run-id run_one \
  --run-id run_two \
  --judge-command-json '["python", "/path/to/judge.py"]'
```

The Python package remains importable as `kimi_coding_agent_flywheel`; existing
imports such as these are supported:

```python
from kimi_coding_agent_flywheel.clustering.failure_analyzer import (
    FailureAnalysisPipeline,
    FailureClusteringEngine,
    LLMJudgeDiagnoser,
)
from kimi_coding_agent_flywheel.core.aq_adapter import AQDbAdapter
```

The higher-level benchmark, optimization, regression, and monitoring modules
are experimental Python APIs. They are not additional CLI subcommands.

See [`docs/QUALITY_FLYWHEEL_GUIDE.md`](docs/QUALITY_FLYWHEEL_GUIDE.md) for the
data model and current implementation boundaries.

## Source-layout migration

The import package now lives under `src/kimi_coding_agent_flywheel/` and is
discovered automatically by setuptools. Installed imports and the
`aq-flywheel` entry point are unchanged. Tools that previously imported the
repository checkout directly should install the project in editable mode (or
add `src/` to their Python path); they should not add the project directory
itself to `sys.path`.
