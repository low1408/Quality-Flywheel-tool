from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from agent_quality import __version__
from agent_quality.collector.envelope import make_envelope, normalize_envelope
from agent_quality.db import connect, insert
from agent_quality.hashutil import sha256_text
from agent_quality.ids import new_id
from agent_quality.timeutil import utc_now
from agent_quality.privacy.redaction import redact_text, redact_json, POLICY_VERSION
from agent_quality.adapters.capability import CODEX_CLI_CAPABILITIES # fallback or base capabilities

ANTIGRAVITY_CAPABILITIES = {
    "prompt_submitted": True,
    "assistant_output": True,
    "reasoning_summaries": True,
    "tool_started": True,
    "tool_completed": True,
    "file_mutations": True,
    "artifact_events": False,
    "token_usage": True,
}

MARKDOWN_FILE_LINK_RE = re.compile(r"\[[^\]]+\]\((/[^)\n]+?)(?::(\d+))?\)")


def ingest_hook_event(event_name: str, payload: dict[str, Any], *, db_path: Path | None = None) -> str:
    # 1. Apply authoritative redaction/sanitization
    redacted_result = redact_json(payload)
    payload = redacted_result.value
    redaction_findings = redacted_result.findings

    # 2. Extract correlation details
    run_id = _first_string(payload, "run_id", "runId") or os.environ.get("AGENT_QUALITY_RUN_ID")
    session_id = _first_string(payload, "session_id", "sessionId", "thread_id", "threadId")
    tool_name = _first_string(payload, "tool_name", "toolName", "tool", "name")
    command = _first_string(payload, "command", "cmd")
    exit_code = _first_int(payload, "exit_code", "exitCode", "status_code", "statusCode")
    assistant_output = _assistant_output(event_name, payload)
    tool_output = _tool_output(event_name, payload)
    tool_input = _tool_input(payload)
    tool_call_id = _first_string(payload, "tool_use_id", "toolUseId", "call_id", "callId")
    file_links = _file_links(payload, assistant_output)
    artifacts = _artifacts(payload)
    primary_path = _first_string(payload, "path", "file", "file_path", "filePath") or _first_path(file_links, artifacts)

    # 3. Determine event details
    status = _status(event_name, payload, exit_code)
    item_type = "assistant_output" if assistant_output else _item_type(event_name)
    tool_category = _tool_category(tool_name, command)

    data = {
        "status": status,
        "item_type": item_type,
        "tool_category": tool_category,
        "command": command,
        "exit_code": exit_code,
        "path": primary_path,
        "duration_ms": _first_int(payload, "duration_ms", "durationMs", "elapsed_ms", "elapsedMs"),
        "hook_event": event_name,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
    }
    if assistant_output:
        data["assistant_output"] = assistant_output
    if tool_output:
        data["tool_output"] = tool_output
    if tool_input is not None:
        data["tool_input"] = tool_input
    if file_links:
        data["file_links"] = file_links
    if artifacts:
        data["artifacts"] = artifacts
    prompt = _prompt_text(event_name, payload)
    if prompt:
        run_id = run_id or _prompt_run_id(payload, session_id, prompt)
        data["prompt"] = prompt

    # 4. Generate stable idempotency key to prevent dual hook/stdout duplication
    # Derived deterministically from run_id, event_name/type, and content/sequence
    content_hash = sha256_text(json.dumps(payload, sort_keys=True))
    idempotency_key = f"{run_id or 'unknown'}:{event_name}:{content_hash[:16]}"

    # 5. Insert into Database
    with connect(db_path) as conn:
        if prompt and run_id:
            _store_prompt_run(conn, run_id, session_id, prompt, payload)
        elif not run_id and session_id:
            run_id = _active_run_id(conn, session_id)

        envelope = make_envelope(
            event_type="agent.message" if assistant_output else f"agent.hook.{_slug(event_name)}",
            source_event_type=event_name,
            source_provider="google",
            source_product="antigravity",
            adapter_version="antigravity-hooks-0.1.0",
            session_id=session_id,
            run_id=run_id,
            data=data,
            extensions={"google.antigravity.hook": payload},
        )
        row = normalize_envelope(envelope)
        row["idempotency_key"] = idempotency_key
        row["privacy_status"] = "sanitized"
        row["privacy_policy_version"] = POLICY_VERSION
        row["redaction_findings"] = json.dumps(redaction_findings)

        try:
            insert(conn, "events", row)
        except Exception as exc:
            # If unique constraint on idempotency_key fails, it's a deduplicated event
            if "UNIQUE constraint failed" not in str(exc):
                raise
        
        # Stop indicates run completion
        if event_name == "Stop" and run_id:
            _close_run(conn, run_id, "completed" if assistant_output else "observed", row["observed_at"])

    return row["id"]


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    event_name = argv[0] if argv else os.environ.get("ANTIGRAVITY_HOOK_EVENT", "UnknownHook")
    try:
        text = sys.stdin.read()
        payload = json.loads(text) if text.strip() else {}
        event_id = ingest_hook_event(event_name, payload)
        print(json.dumps({"ok": True, "event_id": event_id}))
        return 0
    except Exception as exc:
        spool_dir = Path(os.environ.get("AGENT_QUALITY_SPOOL", ".agent-quality/local/spool"))
        spool_dir.mkdir(parents=True, exist_ok=True)
        spool_path = spool_dir / f"antigravity-failed-{new_id('evt')}.json"
        spool_path.write_text(json.dumps({"event": event_name, "error": str(exc)}, sort_keys=True), encoding="utf-8")
        print(json.dumps({"ok": False, "spooled": str(spool_path)}), file=sys.stderr)
        return 0


def rows_from_jsonl(lines: Iterable[str], *, run_id: str, session_id: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # If the output is a single JSON object (representing the complete structured report), parse it directly
    text_buffer = "".join(lines).strip()
    if text_buffer.startswith("{") and text_buffer.endswith("}"):
        try:
            raw = json.loads(text_buffer)
            # If it's a full run summary, map it to a stop/summary event
            raw_redacted = redact_json(raw).value
            envelope = make_envelope(
                event_type="agent.message",
                source_event_type="RunSummary",
                data={
                    "status": "completed" if raw_redacted.get("exit_code") in (0, None) else "failed",
                    "item_type": "assistant_output",
                    "assistant_output": raw_redacted.get("output") or raw_redacted.get("response") or "",
                },
                run_id=run_id,
                session_id=session_id,
                sequence=1,
                extensions={"google.antigravity": raw_redacted},
            )
            rows.append(normalize_envelope(envelope))
            return rows
        except json.JSONDecodeError:
            pass

    # Fallback: parse JSON lines if it outputs events step by step
    for sequence, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            raw = json.loads(stripped)
        except json.JSONDecodeError:
            raw = {"type": "antigravity.stderr", "text": stripped}
        
        redacted_res = redact_json(raw)
        raw_redact = redacted_res.value
        
        # Map raw event to envelope
        kind = str(raw_redact.get("type") or raw_redact.get("event") or "antigravity.event")
        status = raw_redact.get("status", "observed")
        exit_code = raw_redact.get("exit_code")
        command = raw_redact.get("command")
        path = raw_redact.get("path")
        
        envelope = make_envelope(
            event_type="agent.tool.completed" if exit_code is not None else "agent.event",
            source_event_type=kind,
            data={
                "status": status,
                "command": command,
                "exit_code": exit_code,
                "path": path,
            },
            run_id=run_id,
            session_id=session_id,
            sequence=sequence,
            extensions={"google.antigravity": raw_redact},
        )
        row = normalize_envelope(envelope)
        row["privacy_status"] = "sanitized"
        row["privacy_policy_version"] = POLICY_VERSION
        row["redaction_findings"] = json.dumps(redacted_res.findings)
        rows.append(row)
        
    return rows


def extract_usage(raw_lines: Iterable[str]) -> tuple[int | None, int | None, int | None]:
    # Extract token usage if outputted in JSON
    input_tokens = cached_input_tokens = output_tokens = None
    text_buffer = "".join(raw_lines).strip()
    if text_buffer.startswith("{") and text_buffer.endswith("}"):
        try:
            raw = json.loads(text_buffer)
            usage = raw.get("usage") or raw.get("tokens") or {}
            input_tokens = usage.get("input_tokens") or usage.get("prompt_tokens")
            output_tokens = usage.get("output_tokens") or usage.get("completion_tokens")
        except json.JSONDecodeError:
            pass
    return input_tokens, cached_input_tokens, output_tokens


def _first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _deep_get(payload, key)
        if isinstance(value, str) and value:
            return value
    return None


def _first_int(payload: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _deep_get(payload, key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.lstrip("-").isdigit():
            return int(value)
    return None


def _deep_get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _deep_get(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _deep_get(child, key)
            if found is not None:
                return found
    return None


def _prompt_text(event_name: str, payload: dict[str, Any]) -> str | None:
    if event_name not in {"UserPromptSubmit", "PreInvocation"}:
        return None
    return _first_string(payload, "prompt", "message", "text", "content", "input")


def _prompt_run_id(payload: dict[str, Any], session_id: str | None, prompt: str) -> str:
    existing = _first_string(payload, "event_id", "eventId", "id")
    if existing:
        return f"run_{sha256_text(existing)[:32]}"
    if session_id:
        return f"run_{sha256_text(f'{session_id}:{sha256_text(prompt)}')[:32]}"
    return new_id("run")


def _assistant_output(event_name: str, payload: dict[str, Any]) -> str | None:
    if event_name not in {"Stop", "PostInvocation", "AssistantMessage", "AgentMessage"}:
        return None
    return _first_string(payload, "last_assistant_message", "assistant_message", "output", "response", "message", "text", "content")


def _tool_output(event_name: str, payload: dict[str, Any]) -> str | None:
    if event_name != "PostToolUse":
        return None
    return _first_string(payload, "tool_response", "toolResponse", "stdout", "stderr", "output", "response")


def _tool_input(payload: dict[str, Any]) -> Any:
    for key in ("tool_input", "toolInput", "arguments", "parameters"):
        if key in payload:
            return payload[key]
    return None


def _file_links(payload: dict[str, Any], assistant_output: str | None = None) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None]] = set()

    def add(path: str, line: int | None = None) -> None:
        path = path.strip()
        if not path or not path.startswith("/"):
            return
        key = (path, line)
        if key in seen:
            return
        seen.add(key)
        item: dict[str, Any] = {"path": path}
        if line is not None:
            item["line"] = line
        links.append(item)

    for text in (assistant_output, _prompt_text("UserPromptSubmit", payload)):
        if not text:
            continue
        for match in MARKDOWN_FILE_LINK_RE.finditer(text):
            add(match.group(1), int(match.group(2)) if match.group(2) else None)

    for key in ("path", "file", "file_path", "filePath"):
        value = _deep_get(payload, key)
        if isinstance(value, str):
            add(_strip_line_suffix(value))
    return links


def _artifacts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(path: str, artifact_type: str) -> None:
        path = path.strip()
        if not path or not path.startswith("/") or path in seen:
            return
        seen.add(path)
        artifacts.append({"artifact_type": artifact_type, "path": path})

    for key in ("artifact_path", "artifactPath", "log_path", "logPath"):
        value = _deep_get(payload, key)
        if isinstance(value, str):
            add(_strip_line_suffix(value), "hook_artifact")
    return artifacts


def _first_path(*groups: list[dict[str, Any]]) -> str | None:
    for group in groups:
        for item in group:
            path = item.get("path")
            if isinstance(path, str) and path:
                return path
    return None


def _strip_line_suffix(path: str) -> str:
    if ":" not in path:
        return path
    prefix, suffix = path.rsplit(":", 1)
    return prefix if suffix.isdigit() else path


def _status(event_name: str, payload: dict[str, Any], exit_code: int | None) -> str:
    explicit = _first_string(payload, "status", "state")
    if explicit:
        return explicit
    if event_name.startswith("Pre"):
        return "started"
    if event_name.startswith("Post"):
        return "success" if exit_code in (None, 0) else "failed"
    return "observed"


def _item_type(event_name: str) -> str | None:
    if "ToolUse" in event_name:
        return "command_execution"
    if event_name in ("UserPromptSubmit", "PreInvocation"):
        return "user_prompt"
    if event_name in ("Stop", "SessionStart"):
        return "lifecycle"
    return None


def _tool_category(tool_name: str | None, command: str | None) -> str | None:
    text = " ".join(part for part in (tool_name, command) if part).lower()
    if not text:
        return None
    if "mcp" in text:
        return "mcp"
    if any(word in text for word in ("edit", "write", "patch", "replace")):
        return "file_edit"
    if any(word in text for word in ("pytest", "npm test", "cargo test", "go test")):
        return "test"
    if command:
        return "shell"
    return "tool"


def _slug(value: str) -> str:
    return "".join([char.lower() if char.isalnum() else "." for char in value]).strip(".")


def _store_prompt_run(conn: Any, run_id: str, session_id: str | None, prompt: str, payload: dict[str, Any]) -> None:
    started_at = _first_string(payload, "occurred_at", "timestamp", "created_at") or utc_now()
    repo_path = str(Path.cwd().resolve())
    if session_id:
        conn.execute(
            """
            INSERT OR IGNORE INTO sessions (
                id, repository_path, repository_remote_hash, started_at, ended_at, final_outcome, task_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, repo_path, None, started_at, None, None, prompt[:240]),
        )
        turn_number = (
            conn.execute("SELECT COALESCE(MAX(turn_number), 0) + 1 AS n FROM runs WHERE session_id=?", [session_id])
            .fetchone()["n"]
        )
    else:
        turn_number = 1

    insert(
        conn,
        "runs",
        {
            "id": run_id,
            "session_id": session_id,
            "turn_number": turn_number,
            "prompt": prompt,
            "prompt_hash": sha256_text(prompt),
            "repository_path": repo_path,
            "base_commit": "unknown",
            "resulting_commit": None,
            "model": _first_string(payload, "model", "modelId"),
            "agent_adapter": "antigravity",
            "agent_version": None,
            "wrapper_version": __version__,
            "codex_config_hash": None,
            "agents_md_hash": None,
            "verifier_version": None,
            "started_at": started_at,
            "completed_at": None,
            "duration_ms": None,
            "agent_status": "prompt_submitted",
            "verifier_status": "unverified",
            "human_status": "not_reviewed",
            "lifecycle_status": "still_open",
            "input_tokens": None,
            "cached_input_tokens": None,
            "output_tokens": None,
        },
        or_action="OR IGNORE",
    )


def _active_run_id(conn: Any, session_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT id
        FROM runs
        WHERE session_id=?
        ORDER BY started_at DESC, rowid DESC
        LIMIT 1
        """,
        [session_id],
    ).fetchone()
    return row["id"] if row else None


def _close_run(conn: Any, run_id: str, status: str, completed_at: str) -> None:
    conn.execute(
        """
        UPDATE runs
        SET completed_at=COALESCE(completed_at, ?),
            agent_status=CASE
                WHEN agent_status IN ('failed', 'timed_out') THEN agent_status
                ELSE ?
            END,
            lifecycle_status='closed'
        WHERE id=?
        """,
        [completed_at, status, run_id],
    )


if __name__ == "__main__":
    raise SystemExit(main())
