from __future__ import annotations

import json
from typing import Any, Iterable

from agent_quality.adapters.capability import CODEX_CLI_CAPABILITIES
from agent_quality.collector.envelope import make_envelope, normalize_envelope


def event_kind(raw: dict[str, Any]) -> str:
    return str(raw.get("type") or raw.get("event") or raw.get("msg", {}).get("type") or "codex.event")


def extract_command(raw: dict[str, Any]) -> str | None:
    for key in ("command", "cmd"):
        value = raw.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return " ".join(str(part) for part in value)
    msg = raw.get("msg")
    if isinstance(msg, dict):
        return extract_command(msg)
    item = raw.get("item")
    if isinstance(item, dict):
        return extract_command(item)
    return None


def classify_tool(command: str | None, raw: dict[str, Any]) -> str | None:
    tool = str(raw.get("tool") or raw.get("tool_name") or raw.get("name") or "").lower()
    text = " ".join(part for part in (tool, command or "") if part).lower()
    if "apply_patch" in text or "edit" in text or "write" in text:
        return "file_edit"
    if text.startswith("git ") or " git " in text:
        return "vcs"
    if any(word in text for word in ("pytest", "npm test", "cargo test", "go test", "mvn test")):
        return "test"
    if command:
        return "shell"
    return None


def map_raw_event(raw: dict[str, Any], *, run_id: str, session_id: str | None, sequence: int) -> dict[str, Any]:
    kind = event_kind(raw)
    command = extract_command(raw)
    status = raw.get("status")
    exit_code = raw.get("exit_code") or raw.get("code")
    path = raw.get("path") or raw.get("file")
    duration_ms = raw.get("duration_ms")

    lowered = kind.lower()
    if "error" in lowered:
        event_type = "agent.error"
        item_type = "error"
        status = status or "failed"
    elif command and ("complete" in lowered or "completed" in lowered or exit_code is not None):
        event_type = "agent.tool.completed"
        item_type = "command_execution"
        status = status or ("success" if exit_code in (0, "0", None) else "failed")
    elif command:
        event_type = "agent.tool.started"
        item_type = "command_execution"
        status = status or "started"
    elif path or "file" in lowered or "patch" in lowered:
        event_type = "agent.file.changed"
        item_type = "file_change"
        status = status or "completed"
    elif "token" in lowered or "usage" in lowered:
        event_type = "agent.usage"
        item_type = "token_usage"
    elif "assistant" in lowered or "message" in lowered:
        event_type = "agent.message"
        item_type = "assistant_output"
    else:
        event_type = "agent.event"
        item_type = None

    data = {
        "status": status,
        "item_type": item_type,
        "tool_category": classify_tool(command, raw),
        "command": command,
        "exit_code": int(exit_code) if str(exit_code).lstrip("-").isdigit() else None,
        "path": path,
        "duration_ms": int(duration_ms) if str(duration_ms).isdigit() else None,
        "raw_type": kind,
    }
    return make_envelope(
        event_type=event_type,
        source_event_type=kind,
        data=data,
        run_id=run_id,
        session_id=session_id,
        sequence=sequence,
        extensions={"openai.codex": {"raw": raw, "capabilities": CODEX_CLI_CAPABILITIES}},
    )


def rows_from_jsonl(lines: Iterable[str], *, run_id: str, session_id: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sequence, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            raw = json.loads(stripped)
        except json.JSONDecodeError:
            raw = {"type": "codex.stderr", "text": stripped}
        rows.append(normalize_envelope(map_raw_event(raw, run_id=run_id, session_id=session_id, sequence=sequence)))
    return rows


def extract_usage(raw_lines: Iterable[str]) -> tuple[int | None, int | None, int | None]:
    input_tokens = cached_input_tokens = output_tokens = None
    for line in raw_lines:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        text = json.dumps(raw).lower()
        if "token" not in text and "usage" not in text:
            continue
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else raw
        input_tokens = _first_int(usage, ("input_tokens", "prompt_tokens", "total_input_tokens")) or input_tokens
        cached_input_tokens = _first_int(usage, ("cached_input_tokens", "cache_read_input_tokens")) or cached_input_tokens
        output_tokens = _first_int(usage, ("output_tokens", "completion_tokens", "total_output_tokens")) or output_tokens
    return input_tokens, cached_input_tokens, output_tokens


def _first_int(mapping: Any, keys: tuple[str, ...]) -> int | None:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, int):
            return value
    for value in mapping.values():
        if isinstance(value, dict):
            found = _first_int(value, keys)
            if found is not None:
                return found
    return None
