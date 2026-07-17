# Antigravity integration in Agent Quality

Agent Quality captures Antigravity lifecycle events through one user-level hook installation. Project-local hook installation is not supported.

## Hook installation and project activation

- `aq hooks install --provider antigravity` merges the Agent Quality integration into `~/.gemini/config/hooks.json`.
- `aq hooks status --provider antigravity` verifies that every expected handler exists and that its Python executable is still runnable.
- `aq hooks uninstall --provider antigravity` removes only the top-level `"agent-quality"` integration and preserves unrelated hooks.
- A repository opts in through `aq init`, which creates `.agent-quality/config.yaml` and a git-ignored, path-bound `.agent-quality/local/.initialized` marker.
- Global hooks require both files and skip config-only clones or repositories moved without being reinitialized.
- Hook commands use the installed Python package and do not embed a repository path, `PYTHONPATH`, or `AGENT_QUALITY_HOME`.

## Runtime routing

`agent_quality.adapters.hook_runtime` resolves Antigravity's `workspacePaths`, preferring the canonical repository containing `toolCall.args.Cwd` and then the hook process working directory. It rejects symlinked runtime components and routes the event to:

```text
<repository>/.agent-quality/local/quality.sqlite3
```

Sanitized failures spool on a best-effort basis under the same project-local runtime directory. Provider-hook routing deliberately ignores `AGENT_QUALITY_HOME`; only `aq init` can opt a repository into global capture.

## Event adapter

The globally installed Antigravity integration handles:

- `PostToolUse`
- `PreInvocation`
- `PostInvocation`
- `Stop`

The adapter can parse `PreToolUse` when invoked directly, but the global installer intentionally omits it so passive telemetry never allows, denies, or forces a permission prompt. The documented installed-hook events do not carry the user's prompt or the assistant response, so the adapter creates repository-correlated sessions and lifecycle events but does not invent prompt runs. It sanitizes provider payloads before persistence, records the canonical repository path, and deduplicates only deliveries carrying stable provider occurrence identifiers.

## Verification

Automated coverage verifies safe global configuration merging, surgical uninstall, idempotent installation, malformed-JSON protection, initialized-project routing, multi-workspace selection, project isolation, failure spooling, redaction, and duplicate-event handling.
