# Agent Quality

Local-first quality sidecar for coding-agent runs.

This MVP implements the first foundation from `implementation_details/implemenation_details.md`:

- `aq run` wraps `codex exec --json`, captures JSONL events, stores artifacts, and runs independent verifiers.
- SQLite separates runs, events, verifier results, artifacts, and human reviews.
- Source payloads are redacted before persistence.
- `aq review`, `aq show`, `aq diff`, `aq trace`, `aq report summary`, and `aq promote` provide the first terminal workflow.
- `aq serve-collector` exposes a loopback HTTP ingestion endpoint for hook adapters.

Install locally:

```bash
python3 -m pip install -e .
```

Initialize a measured project:

```bash
aq init --repo /path/to/project
```

Run a Codex-backed task:

```bash
aq run --repo /path/to/project --verify /path/to/project/.agent-quality/verify.yaml "Fix the parser"
```

For smoke testing without Codex, pass a command that emits JSONL:

```bash
aq run --allow-dirty "dry run" --agent-command python3 -c 'print("{\"type\":\"message\",\"text\":\"ok\"}")'
```
