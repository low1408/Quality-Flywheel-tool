from __future__ import annotations

import re


def looks_like_test_command(command: str | None) -> bool:
    if not command:
        return False
    return bool(re.search(r"\b(pytest|go test|cargo test|npm test|pnpm test|yarn test|mvn test|gradle test)\b", command))


def repeated_failed_commands(events: list[dict], max_allowed: int) -> list[str]:
    failures: dict[str, int] = {}
    for event in events:
        if event.get("item_type") != "command_execution":
            continue
        if event.get("exit_code") in (None, 0):
            continue
        command = normalize_command(event.get("command"))
        failures[command] = failures.get(command, 0) + 1
    return [command for command, count in failures.items() if count > max_allowed]


def normalize_command(command: str | None) -> str:
    return re.sub(r"\s+", " ", command or "").strip()
