# Support Antigravity and Antigravity 2.0 in Agent Quality (Revised)

This plan integrates the Google Antigravity platform (the 1.x IDE/CLI and the 2.0 standalone `agy` CLI platform) into the `agent-quality` sidecar. It incorporates feedback from the Quorum Council on event correlation, safe hook merging, CLI schema discrimination, and privacy sanitization.

## User Review Required

> [!IMPORTANT]
> - **Workspace Hook Location:** Workspace-level hooks will be installed under `.agents/hooks.json` in the active repository root. Antigravity automatically detects this file.
> - **CLI Execution Support:** For `aq run`, we wrap `agy` (2.0) using its headless print mode (`agy -p --output-format json --dangerously-skip-permissions "<prompt>"`). For Antigravity 1.x (which is purely TUI/interactive), the sidecar will rely on workspace hooks for telemetry capture.
> - **Safe Hooks Merging:** The hook installer will perform a read-modify-write merge on `.agents/hooks.json` instead of overwriting the file, ensuring we do not disrupt other tools.

## Proposed Changes

### Component: agent-quality CLI & Adapters

#### [NEW] [antigravity.py](file:///home/harry/Documents/Github-Projects/personal-projects/quality-flywheel/agent-quality/src/agent_quality/adapters/antigravity.py)
Create a new adapter module that processes Antigravity events.
- **Hook Event Parsing (stdin)**: Parses JSON payloads sent to stdin from Antigravity hooks.
  - `PreToolUse` -> maps to `agent.tool.started`.
  - `PostToolUse` -> maps to `agent.tool.completed`.
  - `PreInvocation` -> maps to `agent.message` (user prompt / start of turn).
  - `PostInvocation` -> maps to `agent.message` (assistant output / reasoning).
  - `Stop` -> closes the run.
- **CLI Output Parsing (stdout)**: Parses stdout stream from non-interactive `agy -p` executions.
- **Sanitization**: Routes all incoming prompt, tool arguments, and assistant responses through `agent_quality.privacy.redaction.redact_text` and `redact_json` before database insertion.
- **Deduplication**: Assigns a deterministic `idempotency_key` (derived from `run_id`, `event_type`, sequence number, and timestamp hash) to prevent duplicate database writes if an event is captured by both hook triggers and stdout wrapping.

#### [MODIFY] [cli.py](file:///home/harry/Documents/Github-Projects/personal-projects/quality-flywheel/agent-quality/src/agent_quality/cli.py)
- Import `antigravity` hook parser.
- Implement `_install_antigravity_hooks`:
  - Locates repository root.
  - Reads `.agents/hooks.json` if it exists.
  - Parses existing JSON and merges the `"agent-quality"` hooks key:
    ```json
    {
      "agent-quality": {
        "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "python3 -m agent_quality.cli hook antigravity PreToolUse"}]}],
        "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "python3 -m agent_quality.cli hook antigravity PostToolUse"}]}],
        "PreInvocation": [{"hooks": [{"type": "command", "command": "python3 -m agent_quality.cli hook antigravity PreInvocation"}]}],
        "PostInvocation": [{"hooks": [{"type": "command", "command": "python3 -m agent_quality.cli hook antigravity PostInvocation"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "python3 -m agent_quality.cli hook antigravity Stop", "timeout": 30}]}]
      }
    }
    ```
  - Writes back safely, preserving other keys.
- Update the `hook` subparser to allow `aq hook antigravity <event>`.

#### [MODIFY] [orchestrator.py](file:///home/harry/Documents/Github-Projects/personal-projects/quality-flywheel/agent-quality/src/agent_quality/orchestrator.py)
- Update `_agent_adapter` to recognize `antigravity` or `agy`.
- For `agy`, configure `aq run` to launch `agy -p --output-format json --dangerously-skip-permissions "<prompt>"` in the subprocess.
- Propagate `AGENT_QUALITY_RUN_ID` in the subprocess environment so that background hooks trigger with the correct run correlation ID.

#### [MODIFY] [package.json](file:///home/harry/Documents/Github-Projects/personal-projects/quality-flywheel/agent-quality/vscode-extension/package.json)
- Register `agentQuality.installAntigravityHooks` command.

#### [MODIFY] [extension.js](file:///home/harry/Documents/Github-Projects/personal-projects/quality-flywheel/agent-quality/vscode-extension/src/extension.js)
- Register command listener executing `aq install-antigravity-hooks`.

## Verification Plan

### Automated Tests
- **Unit Tests (`test_cli_antigravity_hooks.py`)**:
  - Test safe merge logic on empty, non-existent, and populated `.agents/hooks.json` files.
  - Mock stdin payloads for Antigravity 1.x and 2.0 hook events and verify correct database ingestion.
  - Verify deterministic `idempotency_key` behavior under duplicate submissions.
  - Verify redaction is applied to sensitive fields in mock payloads.

### Manual Verification
- Execute `aq install-antigravity-hooks` in a repository with existing third-party hooks and check that they are preserved.
- Wrap an interactive session: Run `antigravity chat "Explain sorting"` and verify events from workspace hooks are logged under the active run in the local SQLite db.
- Wrap a non-interactive run: Run `aq run --agent-command agy -p "Explain sorting"` and verify structured events are parsed, deduplicated, and stored.
