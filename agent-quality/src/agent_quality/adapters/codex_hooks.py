from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from agent_quality.collector.envelope import make_envelope, normalize_envelope
from agent_quality.db import connect, insert
from agent_quality.ids import new_id


def ingest_hook_event(event_name: str, payload: dict[str, Any], *, db_path: Path | None = None) -> str:
    run_id = _first_string(payload, "run_id", "runId")
    session_id = _first_string(payload, "session_id", "sessionId", "thread_id", "threadId")
    tool_name = _first_string(payload, "tool_name", "toolName", "tool", "name")
    command = _first_string(payload, "command", "cmd")
    exit_code = _first_int(payload, "exit_code", "exitCode", "status_code", "statusCode")

    data = {
        "status": _status(event_name, payload, exit_code),
        "item_type": _item_type(event_name),
        "tool_category": _tool_category(tool_name, command),
        "command": command,
        "exit_code": exit_code,
        "path": _first_string(payload, "path", "file", "file_path", "filePath"),
        "duration_ms": _first_int(payload, "duration_ms", "durationMs", "elapsed_ms", "elapsedMs"),
        "hook_event": event_name,
        "tool_name": tool_name,
    }
    envelope = make_envelope(
        event_type=f"agent.hook.{_slug(event_name)}",
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
    with connect(db_path) as conn:
        try:
            insert(conn, "events", row)
        except Exception as exc:
            if "UNIQUE constraint failed" not in str(exc):
                raise
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
