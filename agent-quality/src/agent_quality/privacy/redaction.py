from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

POLICY_VERSION = "redaction-1.0"

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]{20,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
]


@dataclass(frozen=True)
class RedactionResult:
    value: Any
    findings: list[str]


def redact_text(value: str) -> RedactionResult:
    findings: list[str] = []
    redacted = value
    for name, pattern in _PATTERNS:
        redacted, count = pattern.subn(f"[REDACTED:{name}]", redacted)
        if count:
            findings.append(name)
    return RedactionResult(redacted, findings)


def redact_json(value: Any) -> RedactionResult:
    findings: list[str] = []

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            result = redact_text(node)
            findings.extend(result.findings)
            return result.value
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, dict):
            clean: dict[str, Any] = {}
            for key, item in node.items():
                if re.search(r"(?i)(api[_-]?key|token|secret|password|credential)", str(key)):
                    clean[str(key)] = "[REDACTED:field]"
                    findings.append("sensitive_field")
                else:
                    clean[str(key)] = walk(item)
            return clean
        return node

    return RedactionResult(walk(value), sorted(set(findings)))


def sanitized_json_dumps(value: Any) -> tuple[str, list[str]]:
    result = redact_json(value)
    return json.dumps(result.value, sort_keys=True, separators=(",", ":")), result.findings
