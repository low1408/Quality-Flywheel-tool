from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_verify_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"version": 1}
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
        return loaded or {"version": 1}
    except Exception:
        return _parse_tiny_yaml(text)


def _parse_tiny_yaml(text: str) -> dict[str, Any]:
    """Small fallback parser for the verify.yaml shape used by the MVP."""
    result: dict[str, Any] = {"version": 1}
    current_section: str | None = None
    current_item: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" ") and line.endswith(":"):
            current_section = line[:-1].strip()
            result[current_section] = []
            current_item = None
            continue
        if not line.startswith(" ") and ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = _coerce(value.strip())
            continue
        if current_section is None:
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            current_item = {}
            result.setdefault(current_section, []).append(current_item)
            rest = stripped[2:]
            if ":" in rest:
                key, value = rest.split(":", 1)
                current_item[key.strip()] = _coerce(value.strip())
        elif current_item is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current_item[key.strip()] = _coerce(value.strip())
    return result


def _coerce(value: str) -> Any:
    if value in ("true", "false"):
        return value == "true"
    if value.isdigit():
        return int(value)
    return value.strip("'\"")


def verifier_commands(config: dict[str, Any]) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for category, entries in (
        ("acceptance", config.get("acceptance")),
        ("regression", config.get("regression")),
        ("static", config.get("static")),
    ):
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and entry.get("command"):
                    commands.append(
                        {
                            "category": "lint" if category == "static" and "lint" in str(entry.get("name", "")) else category,
                            "name": str(entry.get("name") or entry["command"]),
                            "command": str(entry["command"]),
                            "timeout_seconds": int(entry.get("timeout_seconds") or 300),
                        }
                    )
    return commands
