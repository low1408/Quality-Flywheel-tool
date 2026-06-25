from __future__ import annotations

import json
from typing import Any

from agent_quality.ids import new_id
from agent_quality.privacy.redaction import POLICY_VERSION, sanitized_json_dumps
from agent_quality.timeutil import utc_now

SCHEMA_VERSION = "1.0"


def normalize_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    source = envelope.get("source") or {}
    correlation = envelope.get("correlation") or {}
    data = envelope.get("data") or {}
    extensions = envelope.get("extensions") or {}
    sanitized_source, findings = sanitized_json_dumps(envelope)
    normalized_payload, payload_findings = sanitized_json_dumps(data)
    findings = sorted(set([*findings, *payload_findings]))

    event_id = envelope.get("event_id") or envelope.get("id") or new_id("evt")
    event_type = envelope.get("event_type") or data.get("event_type") or "agent.event"
    observed_at = envelope.get("observed_at") or utc_now()
    privacy = envelope.get("privacy") or {}

    return {
        "id": event_id,
        "schema_version": str(envelope.get("schema_version") or SCHEMA_VERSION),
        "event_type": event_type,
        "source_provider": source.get("provider", "unknown"),
        "source_product": source.get("product"),
        "source_event_type": source.get("source_event_type", event_type),
        "adapter_version": source.get("adapter_version", "unknown"),
        "session_id": correlation.get("session_id"),
        "run_id": correlation.get("run_id"),
        "turn_id": correlation.get("turn_id"),
        "parent_event_id": correlation.get("parent_event_id"),
        "sequence_number": correlation.get("sequence"),
        "occurred_at": envelope.get("occurred_at"),
        "observed_at": observed_at,
        "status": data.get("status"),
        "item_type": data.get("item_type"),
        "tool_category": data.get("tool_category"),
        "command": data.get("command"),
        "exit_code": data.get("exit_code"),
        "path": data.get("path"),
        "duration_ms": data.get("duration_ms"),
        "normalized_payload": normalized_payload,
        "source_payload_sanitized": sanitized_source,
        "provider_extensions": json.dumps(extensions, sort_keys=True, separators=(",", ":")),
        "privacy_status": privacy.get("status", "sanitized"),
        "privacy_policy_version": privacy.get("policy_version", POLICY_VERSION),
        "redaction_findings": json.dumps(findings, sort_keys=True),
        "normalization_status": "normalized",
        "idempotency_key": envelope.get("idempotency_key") or event_id,
    }


def make_envelope(
    *,
    event_type: str,
    source_event_type: str,
    data: dict[str, Any],
    run_id: str | None = None,
    session_id: str | None = None,
    sequence: int | None = None,
    source_provider: str = "openai",
    source_product: str = "codex",
    adapter_version: str = "codex-cli-0.1.0",
    extensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": new_id("evt"),
        "event_type": event_type,
        "occurred_at": utc_now(),
        "observed_at": utc_now(),
        "source": {
            "provider": source_provider,
            "product": source_product,
            "source_event_type": source_event_type,
            "adapter_version": adapter_version,
        },
        "correlation": {
            "session_id": session_id,
            "run_id": run_id,
            "sequence": sequence,
        },
        "data": data,
        "extensions": extensions or {},
        "privacy": {
            "policy_version": POLICY_VERSION,
            "status": "sanitized",
            "raw_payload_retained": False,
        },
    }
