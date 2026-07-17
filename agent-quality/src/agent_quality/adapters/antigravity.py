from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

from agent_quality import __version__
from agent_quality.adapters.hook_runtime import (
    HookRuntime,
    resolve_hook_runtime,
    spool_hook_failure,
)
from agent_quality.collector.envelope import make_envelope, normalize_envelope
from agent_quality.db import connect, insert
from agent_quality.hashutil import sha256_text
from agent_quality.ids import new_id
from agent_quality.privacy.redaction import POLICY_VERSION, redact_json
from agent_quality.timeutil import utc_now

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


def ingest_hook_event(
    event_name: str,
    payload: dict[str, Any],
    *,
    db_path: Path | None = None,
    repository_path: Path | str | None = None,
) -> str:
    repository = Path(repository_path or Path.cwd()).expanduser().resolve()
    # 1. Apply authoritative redaction/sanitization
    redacted_result = redact_json(payload)
    payload = redacted_result.value
    redaction_findings = redacted_result.findings

    # 2. Extract the documented Antigravity contract before considering
    # compatibility aliases used by older captured fixtures.
    run_id = _first_string(payload, "run_id", "runId") or os.environ.get("AGENT_QUALITY_RUN_ID")
    conversation_id = _direct_string(payload, "conversationId")
    session_id = conversation_id or _first_string(payload, "session_id", "sessionId", "thread_id", "threadId")
    workspace_paths = _workspace_paths(payload)
    step_idx = _direct_int(payload, "stepIdx")
    invocation_num = _direct_int(payload, "invocationNum")
    initial_num_steps = _direct_int(payload, "initialNumSteps")
    execution_num = _direct_int(payload, "executionNum")
    termination_reason = _direct_string(payload, "terminationReason")
    fully_idle = _direct_bool(payload, "fullyIdle")
    # Antigravity uses an empty string as the successful PostToolUse/Stop
    # sentinel. Keep that exact value in the sanitized source envelope while
    # normalizing the semantic event field to ``None``.
    error = _direct_string(payload, "error")
    transcript_path = _direct_string(payload, "transcriptPath")
    artifact_directory_path = _direct_string(payload, "artifactDirectoryPath")

    tool_call = payload.get("toolCall") if isinstance(payload.get("toolCall"), dict) else {}
    tool_args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else None
    tool_name = _string_value(tool_call.get("name")) or _first_string(
        payload, "tool_name", "toolName", "tool"
    )
    command = _tool_arg_string(tool_args, "CommandLine", "commandLine", "command") or _first_string(
        payload, "command", "cmd"
    )
    tool_cwd = _tool_arg_string(tool_args, "Cwd", "cwd")
    exit_code = _first_int(payload, "exit_code", "exitCode", "status_code", "statusCode")
    if exit_code is None:
        exit_code = _exit_code_from_error(error)
    assistant_output = _assistant_output(event_name, payload)
    tool_output = _tool_output(event_name, payload)
    tool_input = tool_args if tool_args is not None else _tool_input(payload)
    tool_call_id = _first_string(payload, "tool_use_id", "toolUseId", "call_id", "callId")
    if not tool_call_id and session_id and step_idx is not None:
        tool_call_id = f"agtool_{sha256_text(f'{session_id}:{step_idx}')[:32]}"
    file_links = _file_links(payload, assistant_output)
    artifacts = _artifacts(payload)
    primary_path = (
        tool_cwd
        or _first_string(payload, "path", "file", "file_path", "filePath")
        or _first_path(file_links, artifacts)
    )

    # 3. Determine event details
    status = _status(
        event_name,
        payload,
        exit_code,
        error=error,
        fully_idle=fully_idle,
        termination_reason=termination_reason,
    )
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
        "conversation_id": conversation_id,
        "workspace_paths": workspace_paths,
        "transcript_path": transcript_path,
        "artifact_directory_path": artifact_directory_path,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "tool_cwd": tool_cwd,
        "step_idx": step_idx,
        "invocation_num": invocation_num,
        "initial_num_steps": initial_num_steps,
        "execution_num": execution_num,
        "termination_reason": termination_reason,
        "fully_idle": fully_idle,
        "error": error,
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

    # 4. Generate a stable idempotency key only from identifiers supplied by
    # Antigravity. Hashing the whole payload would collapse legitimate repeats.
    idempotency_key = _hook_idempotency_key(event_name, payload, session_id=session_id)

    # 5. Insert into Database
    with connect(db_path) as conn:
        if session_id and not prompt:
            _ensure_session(conn, session_id, payload, repository_path=repository)
        if prompt and run_id:
            _store_prompt_run(conn, run_id, session_id, prompt, payload, repository_path=repository)
        elif not run_id and session_id:
            run_id = _active_run_id(conn, session_id)

        if event_name == "PostToolUse" and session_id and step_idx is not None:
            correlated = _correlated_pre_tool_data(conn, session_id, step_idx)
            for key in ("tool_name", "tool_call_id", "tool_cwd", "tool_input", "command", "path"):
                if data.get(key) is None and correlated.get(key) is not None:
                    data[key] = correlated[key]
            data["tool_category"] = _tool_category(data.get("tool_name"), data.get("command"))

        envelope = make_envelope(
            event_type="agent.message" if assistant_output else f"agent.hook.{_slug(event_name)}",
            source_event_type=event_name,
            source_provider="google",
            source_product="antigravity",
            adapter_version="antigravity-hooks-0.1.0",
            session_id=session_id,
            run_id=run_id,
            sequence=_event_sequence(event_name, step_idx, invocation_num, execution_num),
            data=data,
            extensions={"google.antigravity.hook": payload},
        )
        row = normalize_envelope(envelope)
        row["idempotency_key"] = idempotency_key
        row["privacy_status"] = "sanitized"
        row["privacy_policy_version"] = POLICY_VERSION
        row["redaction_findings"] = json.dumps(redaction_findings)
        event_id = row["id"]

        inserted = False
        try:
            insert(conn, "events", row)
            inserted = True
        except sqlite3.IntegrityError as exc:
            # Only the idempotency constraint represents a duplicate delivery.
            if (
                idempotency_key is None
                or "UNIQUE constraint failed: events.idempotency_key" not in str(exc)
            ):
                raise
            existing = conn.execute(
                "SELECT id FROM events WHERE idempotency_key=?",
                [idempotency_key],
            ).fetchone()
            if existing is None:
                raise
            event_id = existing["id"]

        # Stop indicates conversation and any correlated run completion.
        if inserted and event_name == "Stop":
            completion_status = "failed" if status == "failed" else "completed"
            if session_id:
                _close_session(conn, session_id, completion_status, row["observed_at"])
            if run_id:
                _close_run(conn, run_id, completion_status, row["observed_at"])

    return event_id


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    event_name = argv[0] if argv else os.environ.get("ANTIGRAVITY_HOOK_EVENT", "UnknownHook")
    runtime: HookRuntime | None = None
    payload: dict[str, Any] = {}
    try:
        text = sys.stdin.read()
        parsed = json.loads(text) if text.strip() else {}
        if not isinstance(parsed, dict):
            raise ValueError("hook payload must be a JSON object")
        payload = parsed
        runtime = resolve_hook_runtime("antigravity", payload)
        if runtime is not None:
            ingest_hook_event(
                event_name,
                payload,
                db_path=runtime.db_path,
                repository_path=runtime.repository_path,
            )
    except Exception as exc:
        if runtime is None:
            try:
                runtime = resolve_hook_runtime("antigravity", payload)
            except Exception:
                runtime = None
        try:
            spool_path = spool_hook_failure(
                runtime,
                provider="antigravity",
                event_name=event_name,
                payload=payload,
                error=exc,
            )
            print(
                json.dumps({"ok": False, "spooled": str(spool_path) if spool_path else False}),
                file=sys.stderr,
            )
        except Exception:
            # The provider hook must remain fail-open even if diagnostic storage
            # itself encounters an unexpected failure.
            pass

    # Antigravity validates hook stdout. Telemetry success, project opt-out, and
    # internal failures must all return the same provider-valid, fail-open shape.
    print(json.dumps(_hook_response(event_name), separators=(",", ":")))
    return 0


def _hook_response(event_name: str) -> dict[str, str]:
    if event_name == "PreToolUse":
        # There is no neutral PreToolUse response in Antigravity's contract.
        # Agent Quality does not install this event globally; if someone invokes
        # it directly, asking is safer than silently allowing or denying a tool.
        return {
            "decision": "ask",
            "reason": "Agent Quality telemetry does not make tool permission decisions.",
        }
    if event_name == "Stop":
        return {"decision": "allow"}
    return {}


def _hook_idempotency_key(
    event_name: str,
    payload: dict[str, Any],
    *,
    session_id: str | None,
) -> str | None:
    provider_event_id = _direct_first_string(
        payload,
        "eventId",
        "event_id",
        "idempotencyKey",
        "idempotency_key",
    )
    sequence_kind: str | None = None
    sequence: int | None = None
    if event_name in {"PreToolUse", "PostToolUse"}:
        sequence_kind, sequence = "stepIdx", _direct_int(payload, "stepIdx")
    elif event_name in {"PreInvocation", "PostInvocation"}:
        sequence_kind, sequence = "invocationNum", _direct_int(payload, "invocationNum")
    elif event_name == "Stop":
        sequence_kind, sequence = "executionNum", _direct_int(payload, "executionNum")

    # Do not collapse identifier-free events merely because their payloads are
    # identical. Repeated lifecycle events can be legitimate occurrences.
    if provider_event_id is None and (session_id is None or sequence is None):
        return None
    material = json.dumps(
        {
            "event": event_name,
            "provider_event_id": provider_event_id,
            "session_id": session_id,
            "sequence_kind": sequence_kind,
            "sequence": sequence,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"antigravity-hook:{sha256_text(material)}"


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


def _direct_string(payload: dict[str, Any], key: str) -> str | None:
    return _string_value(payload.get(key))


def _direct_first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _direct_string(payload, key)
        if value is not None:
            return value
    return None


def _string_value(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _direct_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _direct_bool(payload: dict[str, Any], key: str) -> bool | None:
    value = payload.get(key)
    return value if isinstance(value, bool) else None


def _workspace_paths(payload: dict[str, Any]) -> list[str]:
    value = payload.get("workspacePaths")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _tool_arg_string(tool_args: dict[str, Any] | None, *keys: str) -> str | None:
    if tool_args is None:
        return None
    for key in keys:
        value = _string_value(tool_args.get(key))
        if value is not None:
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
    # Antigravity's documented PreInvocation payload contains trajectory counts,
    # not the user's prompt. Only accept an explicit prompt event here.
    if event_name != "UserPromptSubmit":
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
    tool_call = payload.get("toolCall")
    if isinstance(tool_call, dict) and isinstance(tool_call.get("args"), dict):
        return tool_call["args"]
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


def _status(
    event_name: str,
    payload: dict[str, Any],
    exit_code: int | None,
    *,
    error: str | None = None,
    fully_idle: bool | None = None,
    termination_reason: str | None = None,
) -> str:
    explicit = _first_string(payload, "status", "state")
    if explicit:
        return explicit
    if error:
        return "failed"
    if event_name == "Stop" and termination_reason:
        normalized_reason = termination_reason.lower()
        if "error" in normalized_reason or "fail" in normalized_reason:
            return "failed"
    if event_name.startswith("Pre"):
        return "started"
    if event_name.startswith("Post"):
        return "success" if exit_code in (None, 0) else "failed"
    if event_name == "Stop":
        return "completed" if fully_idle is not False else "stopping"
    return "observed"


def _item_type(event_name: str) -> str | None:
    if "ToolUse" in event_name:
        return "command_execution"
    if event_name == "UserPromptSubmit":
        return "user_prompt"
    if event_name in ("PreInvocation", "PostInvocation"):
        return "model_invocation"
    if event_name in ("Stop", "SessionStart"):
        return "lifecycle"
    return None


def _event_sequence(
    event_name: str,
    step_idx: int | None,
    invocation_num: int | None,
    execution_num: int | None,
) -> int | None:
    if event_name in {"PreToolUse", "PostToolUse"}:
        return step_idx
    if event_name in {"PreInvocation", "PostInvocation"}:
        return invocation_num
    if event_name == "Stop":
        return execution_num
    return None


def _exit_code_from_error(error: str | None) -> int | None:
    if not error:
        return None
    match = re.search(r"\bexit (?:status|code)\s+(-?\d+)\b", error, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _correlated_pre_tool_data(conn: Any, session_id: str, step_idx: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT normalized_payload
        FROM events
        WHERE session_id=?
          AND source_provider='google'
          AND source_product='antigravity'
          AND source_event_type='PreToolUse'
          AND sequence_number=?
        ORDER BY rowid DESC
        LIMIT 1
        """,
        [session_id, step_idx],
    ).fetchone()
    if row is None:
        return {}
    try:
        data = json.loads(row["normalized_payload"])
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: data[key]
        for key in ("tool_name", "tool_call_id", "tool_cwd", "tool_input", "command", "path")
        if key in data
    }


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


def _ensure_session(
    conn: Any,
    session_id: str,
    payload: dict[str, Any],
    *,
    repository_path: Path,
) -> None:
    started_at = _first_string(payload, "occurred_at", "timestamp", "created_at") or utc_now()
    conn.execute(
        """
        INSERT OR IGNORE INTO sessions (
            id, repository_path, repository_remote_hash, started_at, ended_at, final_outcome, task_summary
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (session_id, str(repository_path), None, started_at, None, None, None),
    )


def _store_prompt_run(
    conn: Any,
    run_id: str,
    session_id: str | None,
    prompt: str,
    payload: dict[str, Any],
    *,
    repository_path: Path,
) -> None:
    started_at = _first_string(payload, "occurred_at", "timestamp", "created_at") or utc_now()
    repo_path = str(repository_path)
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
          AND completed_at IS NULL
          AND COALESCE(lifecycle_status, 'still_open') != 'closed'
        ORDER BY started_at DESC, rowid DESC
        LIMIT 1
        """,
        [session_id],
    ).fetchone()
    return row["id"] if row else None


def _close_session(conn: Any, session_id: str, status: str, completed_at: str) -> None:
    conn.execute(
        """
        UPDATE sessions
        SET ended_at=COALESCE(ended_at, ?),
            final_outcome=COALESCE(final_outcome, ?)
        WHERE id=?
        """,
        [completed_at, status, session_id],
    )


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
