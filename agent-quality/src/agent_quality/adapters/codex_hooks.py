from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent_quality import __version__
from agent_quality.collector.envelope import make_envelope, normalize_envelope
from agent_quality.db import connect, insert
from agent_quality.hashutil import sha256_text
from agent_quality.ids import new_id
from agent_quality.timeutil import utc_now

MARKDOWN_FILE_LINK_RE = re.compile(r"\[[^\]]+\]\((/[^)\n]+?)(?::(\d+))?\)")


def ingest_hook_event(event_name: str, payload: dict[str, Any], *, db_path: Path | None = None) -> str:
    run_id = _first_string(payload, "run_id", "runId")
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

    data = {
        "status": _status(event_name, payload, exit_code),
        "item_type": "assistant_output" if assistant_output else _item_type(event_name),
        "tool_category": _tool_category(tool_name, command),
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

    with connect(db_path) as conn:
        if prompt:
            _store_prompt_run(conn, run_id, session_id, prompt, payload)
        elif not run_id and session_id:
            run_id = _active_run_id(conn, session_id)

        envelope = make_envelope(
            event_type="agent.message" if assistant_output else f"agent.hook.{_slug(event_name)}",
            source_event_type=event_name,
            source_provider="openai",
            source_product="codex",
            adapter_version="codex-hooks-0.1.0",
            session_id=session_id,
            run_id=run_id,
            data=data,
            extensions={"openai.codex.hook": payload},
        )
        row = normalize_envelope(envelope)
        try:
            insert(conn, "events", row)
        except Exception as exc:
            if "UNIQUE constraint failed" not in str(exc):
                raise
        if event_name == "Stop" and run_id:
            _ingest_transcript_reasoning(conn, payload, run_id=run_id, session_id=session_id)
            _close_run(conn, run_id, "completed" if assistant_output else "observed", row["observed_at"])
    return row["id"]


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    event_name = argv[0] if argv else os.environ.get("CODEX_HOOK_EVENT", "UnknownHook")
    try:
        text = sys.stdin.read()
        payload = json.loads(text) if text.strip() else {}
        event_id = ingest_hook_event(event_name, payload)
        print(json.dumps({"ok": True, "event_id": event_id}))
        return 0
    except Exception as exc:
        spool_dir = Path(os.environ.get("AGENT_QUALITY_SPOOL", ".agent-quality/local/spool"))
        spool_dir.mkdir(parents=True, exist_ok=True)
        spool_path = spool_dir / f"hook-failed-{new_id('evt')}.json"
        spool_path.write_text(json.dumps({"event": event_name, "error": str(exc)}, sort_keys=True), encoding="utf-8")
        print(json.dumps({"ok": False, "spooled": str(spool_path)}), file=sys.stderr)
        return 0


def _first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _deep_get(payload, key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list):
            return " ".join(str(item) for item in value)
    return None


def _first_int(payload: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _deep_get(payload, key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.lstrip("-").isdigit():
            return int(value)
    return None


def _prompt_text(event_name: str, payload: dict[str, Any]) -> str | None:
    if event_name != "UserPromptSubmit":
        return None
    value = _first_string(
        payload,
        "prompt",
        "user_prompt",
        "userPrompt",
        "message",
        "text",
        "content",
        "input",
    )
    if value:
        value = value.strip()
    return value or None


def _prompt_run_id(payload: dict[str, Any], session_id: str | None, prompt: str) -> str:
    existing = _first_string(payload, "event_id", "eventId", "id", "idempotency_key", "idempotencyKey")
    sequence = _first_int(payload, "sequence", "sequence_number", "sequenceNumber")
    if existing:
        key = existing
    elif session_id and sequence is not None:
        key = f"{session_id}:{sequence}:{sha256_text(prompt)}"
    else:
        return new_id("run")
    return f"run_{sha256_text(key)[:32]}"


def _assistant_output(event_name: str, payload: dict[str, Any]) -> str | None:
    if event_name not in {"Stop", "AssistantMessage", "AgentMessage"}:
        return None
    value = _first_string(
        payload,
        "last_assistant_message",
        "assistant_message",
        "assistantMessage",
        "assistant_output",
        "assistantOutput",
        "final_response",
        "finalResponse",
        "response",
        "output",
        "message",
        "text",
        "content",
    )
    if value:
        value = value.strip()
    return value or None


def _tool_output(event_name: str, payload: dict[str, Any]) -> str | None:
    if event_name != "PostToolUse":
        return None
    value = _first_string(payload, "tool_response", "toolResponse", "stdout", "stderr", "output", "response")
    if value:
        value = value.strip()
    return value or None


def _tool_input(payload: dict[str, Any]) -> Any:
    for key in ("tool_input", "toolInput", "arguments", "parameters"):
        if key in payload:
            return payload[key]
    return None


def _ingest_transcript_reasoning(
    conn,
    payload: dict[str, Any],
    *,
    run_id: str,
    session_id: str | None,
) -> None:
    transcript = _first_string(payload, "transcript_path", "transcriptPath")
    if not transcript:
        return
    path = Path(transcript).expanduser()
    if not path.is_file():
        return
    run = conn.execute("SELECT prompt FROM runs WHERE id=?", [run_id]).fetchone()
    prompt = str(run["prompt"] or "") if run else ""
    for index, record in enumerate(_transcript_turn_records(path, prompt), start=1):
        trace = _transcript_reasoning_item(record)
        if not trace:
            continue
        source_type, reasoning_kind, text = trace
        stable_source = json.dumps(record, sort_keys=True, separators=(",", ":"))
        envelope = make_envelope(
            event_type="agent.reasoning",
            source_event_type=source_type,
            source_provider="openai",
            source_product="codex",
            adapter_version="codex-transcript-0.1.0",
            session_id=session_id,
            run_id=run_id,
            data={
                "status": "completed",
                "item_type": "reasoning",
                "reasoning": text,
                "reasoning_kind": reasoning_kind,
                "trace_index": index,
            },
            extensions={"openai.codex.transcript": {"type": record.get("type")}},
        )
        envelope["event_id"] = f"evt_trace_{sha256_text(f'{path}:{stable_source}')[:32]}"
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str) and timestamp:
            envelope["occurred_at"] = timestamp
        try:
            insert(conn, "events", normalize_envelope(envelope))
        except Exception as exc:
            if "UNIQUE constraint failed" not in str(exc):
                raise


def _transcript_turn_records(path: Path, prompt: str) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    latest: list[dict[str, Any]] = []
    matching = False
    found_match = False
    saw_user_message = False
    try:
        lines = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return []
    with lines:
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            user_message = _transcript_user_message(record)
            if user_message is not None:
                if found_match:
                    break
                saw_user_message = True
                latest = []
                matching = _same_prompt(user_message, prompt)
                found_match = matching
                continue
            if not saw_user_message:
                continue
            latest.append(record)
            if matching:
                matched.append(record)
    return matched if found_match else latest


def _transcript_user_message(record: dict[str, Any]) -> str | None:
    if record.get("type") != "event_msg":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "user_message":
        return None
    message = payload.get("message")
    return message if isinstance(message, str) else None


def _same_prompt(left: str, right: str) -> bool:
    normalize = lambda value: " ".join(value.split())
    return bool(right) and normalize(left) == normalize(right)


def _transcript_reasoning_item(record: dict[str, Any]) -> tuple[str, str, str] | None:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    if record.get("type") == "response_item" and payload.get("type") == "reasoning":
        text = _transcript_text(payload.get("summary"))
        return ("transcript.reasoning", "summary", text) if text else None
    if record.get("type") == "response_item" and payload.get("type") == "message":
        if payload.get("role") != "assistant" or payload.get("phase") not in {"commentary", "analysis"}:
            return None
        text = _transcript_text(payload.get("content"))
        return ("transcript.commentary", str(payload.get("phase")), text) if text else None
    return None


def _transcript_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, list):
        return None
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text") or item.get("content")
            if isinstance(text, str):
                parts.append(text)
    joined = "\n".join(part.strip() for part in parts if part.strip())
    return joined or None


def _file_links(payload: dict[str, Any], assistant_output: str | None = None) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None]] = set()

    def add(path: str, line: int | None = None, label: str | None = None) -> None:
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
        if label:
            item["label"] = label
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
    for key in ("files", "file_paths", "filePaths"):
        value = _deep_get(payload, key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    add(_strip_line_suffix(item))
                elif isinstance(item, dict):
                    path = _first_string(item, "path", "file", "file_path", "filePath")
                    line = _first_int(item, "line", "line_number", "lineNumber")
                    if path:
                        add(_strip_line_suffix(path), line)
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

    transcript = _first_string(payload, "transcript_path", "transcriptPath")
    if transcript:
        add(transcript, "transcript")
    for key in ("artifact_path", "artifactPath", "log_path", "logPath"):
        value = _deep_get(payload, key)
        if isinstance(value, str):
            add(_strip_line_suffix(value), "hook_artifact")
    for key in ("artifacts", "artifact_paths", "artifactPaths"):
        value = _deep_get(payload, key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    add(_strip_line_suffix(item), "hook_artifact")
                elif isinstance(item, dict):
                    path = _first_string(item, "path", "file", "artifact_path", "artifactPath")
                    artifact_type = _first_string(item, "artifact_type", "artifactType", "type") or "hook_artifact"
                    if path:
                        add(_strip_line_suffix(path), artifact_type)
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


def _store_prompt_run(conn, run_id: str, session_id: str | None, prompt: str, payload: dict[str, Any]) -> None:
    started_at = (
        _first_string(payload, "occurred_at", "occurredAt", "timestamp", "time", "created_at", "createdAt")
        or utc_now()
    )
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
            "base_commit": _head_commit(repo_path),
            "resulting_commit": None,
            "model": _first_string(payload, "model", "model_id", "modelId"),
            "agent_adapter": "codex-hooks",
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


def _active_run_id(conn, session_id: str) -> str | None:
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


def _close_run(conn, run_id: str, status: str, completed_at: str) -> None:
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


def _head_commit(repo_path: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            text=True,
            capture_output=True,
            timeout=2,
        )
    except Exception:
        return "unknown"
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else "unknown"


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


def _item_type(event_name: str) -> str | None:
    if "ToolUse" in event_name or "PermissionRequest" in event_name:
        return "command_execution"
    if event_name == "UserPromptSubmit":
        return "user_prompt"
    if event_name in ("Stop", "SessionStart"):
        return "lifecycle"
    return None


def _status(event_name: str, payload: dict[str, Any], exit_code: int | None) -> str:
    explicit = _first_string(payload, "status", "state")
    if explicit:
        return explicit
    if event_name.startswith("Pre"):
        return "started"
    if event_name.startswith("Post"):
        return "success" if exit_code in (None, 0) else "failed"
    if event_name == "PermissionRequest":
        return "requested"
    return "observed"


def _tool_category(tool_name: str | None, command: str | None) -> str | None:
    text = " ".join(part for part in (tool_name, command) if part).lower()
    if not text:
        return None
    if "mcp__" in text or text.startswith("mcp"):
        return "mcp"
    if "apply_patch" in text or "edit" in text or "write" in text:
        return "file_edit"
    if any(word in text for word in ("pytest", "npm test", "cargo test", "go test")):
        return "test"
    if "bash" in text or command:
        return "shell"
    return "tool"


def _slug(value: str) -> str:
    return "".join([char.lower() if char.isalnum() else "." for char in value]).strip(".")


if __name__ == "__main__":
    raise SystemExit(main())
