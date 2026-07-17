from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TextIO

from agent_quality.collector.dashboard import (
    dashboard_run_details,
    dashboard_runs,
    dashboard_session_details,
    dashboard_sessions,
    read_dashboard_file,
)
from agent_quality.db import all_rows, connect, default_db_path, delete_chat, one
from agent_quality.privacy.redaction import redact_json
from agent_quality.review.service import save_review_api


UI_ACTIONS = (
    "runs",
    "sessions",
    "details",
    "session_details",
    "read_file",
    "save_review",
    "delete_chat",
    "flywheel_candidates",
    "flywheel_analysis_prompt",
    "flywheel_analyses",
    "flywheel_analysis_details",
)

_EMPTY_DATABASE_ACTIONS = frozenset(
    {"runs", "sessions", "flywheel_candidates", "flywheel_analyses"}
)


def run_ui_api(action: str, input_stream: TextIO, output_stream: TextIO) -> None:
    """Run one allowlisted dashboard request over a one-line JSON contract."""

    request_line = input_stream.readline()
    if not request_line.strip():
        payload: dict[str, Any] = {}
    else:
        try:
            decoded = json.loads(request_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid UI API request JSON: {exc}") from exc
        if not isinstance(decoded, dict):
            raise ValueError("UI API request must be a JSON object")
        payload = decoded
    if input_stream.read().strip():
        raise ValueError("UI API accepts exactly one JSON request line")

    response = execute_ui_action(action, payload)
    output_stream.write(json.dumps(response, sort_keys=True, separators=(",", ":")))
    output_stream.write("\n")


def execute_ui_action(
    action: str,
    payload: dict[str, Any],
    *,
    db_path: Path | str | None = None,
) -> object:
    if action not in UI_ACTIONS:
        raise ValueError(f"unknown UI API action: {action}")

    database = Path(db_path).expanduser() if db_path is not None else default_db_path()
    if not database.exists():
        if action in _EMPTY_DATABASE_ACTIONS:
            return []
        raise FileNotFoundError(f"Agent Quality database does not exist yet: {database}")

    if action == "runs":
        return dashboard_runs(database)
    if action == "sessions":
        return dashboard_sessions(database)
    if action == "details":
        return dashboard_run_details(database, _required_string(payload, "run_id"))
    if action == "session_details":
        return dashboard_session_details(database, _required_string(payload, "session_id"))
    if action == "read_file":
        return read_dashboard_file(database, _required_string(payload, "path"))
    if action == "save_review":
        return _save_review(database, payload)
    if action == "delete_chat":
        with connect(database) as conn:
            return delete_chat(conn, _required_string(payload, "chat_id"))
    if action == "flywheel_candidates":
        return _flywheel_candidates(database)
    if action == "flywheel_analysis_prompt":
        return _flywheel_analysis_prompt_response(database, payload)
    if action == "flywheel_analyses":
        with connect(database) as conn:
            return [
                _row_to_dict(row)
                for row in all_rows(
                    conn,
                    "SELECT * FROM analysis_runs ORDER BY created_at DESC, id DESC",
                )
            ]
    return _flywheel_analysis_details(database, payload)


def _save_review(database: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return save_review_api(
        _required_string(payload, "run_id"),
        _required_string(payload, "outcome"),
        primary_category=_optional_string(payload.get("primary_category")),
        severity=_optional_string(payload.get("severity")),
        notes=str(payload.get("notes") or ""),
        confidence=_optional_float(payload.get("confidence")),
        critical_sequence=_optional_int(payload.get("critical_sequence")),
        db_path=database,
    )


def _flywheel_candidates(database: Path) -> list[dict[str, Any]]:
    with connect(database) as conn:
        rows = all_rows(
            conn,
            """
            SELECT
                r.id,
                r.prompt,
                r.started_at,
                r.completed_at,
                r.agent_adapter,
                r.model,
                r.agent_status,
                r.verifier_status,
                r.human_status,
                r.lifecycle_status,
                CASE WHEN
                    lower(COALESCE(r.agent_status, '')) IN ('failed', 'error')
                    OR lower(COALESCE(r.verifier_status, '')) = 'failed'
                    OR lower(COALESCE(r.human_status, '')) IN (
                        'partial', 'rejected', 'accepted_with_major_edits'
                    )
                THEN 1 ELSE 0 END AS default_selected,
                (SELECT COUNT(*) FROM events e WHERE e.run_id=r.id) AS event_count
            FROM runs r
            WHERE (r.completed_at IS NOT NULL OR r.lifecycle_status='closed')
              AND EXISTS (SELECT 1 FROM events e WHERE e.run_id=r.id)
            ORDER BY r.started_at DESC, r.id DESC
            """,
        )
    return [_compact_run_row(row) for row in rows]


def _flywheel_analysis_prompt_response(
    database: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    run_ids = payload.get("run_ids")
    if not isinstance(run_ids, list):
        raise ValueError("run_ids must be a list")
    selected = [run_id for run_id in run_ids if isinstance(run_id, str) and run_id]
    if not selected:
        raise ValueError("select at least one run")

    run_items: list[dict[str, Any]] = []
    with connect(database) as conn:
        for run_id in selected:
            run = one(
                conn,
                """
                SELECT
                    id, prompt, started_at, completed_at, agent_adapter, model,
                    agent_status, verifier_status, human_status, lifecycle_status
                FROM runs
                WHERE id=?
                """,
                [run_id],
            )
            if not run:
                continue
            events = all_rows(
                conn,
                """
                SELECT *
                FROM events
                WHERE run_id=?
                ORDER BY COALESCE(sequence_number, 999999999), occurred_at, observed_at, id
                LIMIT 120
                """,
                [run_id],
            )
            event_count = one(
                conn,
                "SELECT COUNT(*) AS count FROM events WHERE run_id=?",
                [run_id],
            )["count"]
            item = _row_to_dict(run)
            item["events"] = [_flywheel_event_dict(event) for event in events]
            item["event_count"] = event_count
            if event_count > len(events):
                item["events_truncated"] = event_count - len(events)
            run_items.append(item)

    if not run_items:
        raise ValueError("none of the selected runs were found")
    sanitized_runs = redact_json(run_items).value
    prompt = _flywheel_analysis_prompt(sanitized_runs)
    return {
        "prompt": prompt,
        "run_count": len(sanitized_runs),
        "character_count": len(prompt),
    }


def _flywheel_analysis_details(
    database: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    analysis_id = _required_string(payload, "analysis_id")
    with connect(database) as conn:
        analysis = one(conn, "SELECT * FROM analysis_runs WHERE id=?", [analysis_id])
        if not analysis:
            raise KeyError(f"unknown analysis: {analysis_id}")
        inputs = [
            _compact_value(_row_to_dict(row))
            for row in all_rows(
                conn,
                """
                SELECT ai.*, r.prompt, r.started_at, r.agent_adapter, r.model,
                       r.agent_status, r.verifier_status, r.human_status
                FROM analysis_inputs ai
                JOIN runs r ON r.id=ai.run_id
                WHERE ai.analysis_id=?
                ORDER BY r.started_at DESC, r.id DESC
                """,
                [analysis_id],
            )
        ]
        clusters: list[dict[str, Any]] = []
        cluster_rows = all_rows(
            conn,
            """
            SELECT DISTINCT fc.*
            FROM failure_clusters fc
            JOIN failure_cluster_memberships m ON m.cluster_id=fc.id
            WHERE m.analysis_id=?
            ORDER BY fc.occurrence_count DESC, fc.id
            """,
            [analysis_id],
        )
        for row in cluster_rows:
            cluster = _row_to_dict(row)
            cluster["provider_extensions_json"] = _json_or_value(
                cluster.get("provider_extensions") or "{}"
            )
            cluster["affected_runs"] = [
                item["run_id"]
                for item in all_rows(
                    conn,
                    """
                    SELECT DISTINCT run_id
                    FROM failure_cluster_memberships
                    WHERE analysis_id=? AND cluster_id=?
                    ORDER BY run_id
                    """,
                    [analysis_id, cluster["id"]],
                )
            ]
            clusters.append(cluster)
        failures = [
            _row_to_dict(row)
            for row in all_rows(
                conn,
                """
                SELECT *
                FROM failure_instances
                WHERE analysis_id=?
                ORDER BY severity DESC, timestamp, id
                """,
                [analysis_id],
            )
        ]
    return {
        "analysis": _row_to_dict(analysis),
        "inputs": inputs,
        "clusters": clusters,
        "failures": failures,
    }


def _flywheel_event_dict(row: Any) -> dict[str, Any]:
    data = _row_to_dict(row)
    item = {
        key: data.get(key)
        for key in (
            "id",
            "event_type",
            "source_event_type",
            "sequence_number",
            "occurred_at",
            "status",
            "item_type",
            "tool_category",
            "command",
            "exit_code",
            "path",
            "duration_ms",
        )
    }
    for key in ("normalized_payload", "source_payload_sanitized"):
        raw = data.get(key)
        if raw:
            item[key] = _compact_value(_json_or_value(raw), 4000)
    return {key: value for key, value in item.items() if value not in (None, "", [])}


def _flywheel_analysis_prompt(run_items: list[dict[str, Any]]) -> str:
    payload = {
        "runs": run_items,
        "notes": [
            "Payloads come from Agent Quality's persisted sanitized event data.",
            "Large text fields may be truncated.",
        ],
    }
    body = json.dumps(payload, indent=2, sort_keys=True)
    return f"""You are an expert AI systems debugger.

Analyze the selected Agent Quality runs below. Identify concrete agent failures, likely root causes, and recurring failure clusters across runs.

For each run, infer what a competent coding agent should have done, compare it with the actual behavior, and diagnose any failures.

Return Markdown with these sections:
1. Executive Summary
2. Per-Run Diagnoses
3. Cross-Run Failure Clusters
4. Recommended Fixes
5. Regression Test Ideas

Also include this JSON object at the end, inside a fenced JSON code block:
{{
  "runs": [
    {{
      "run_id": "string",
      "overall_score": 0.0,
      "failures": [
        {{
          "subcategory": "string",
          "severity": "low|medium|high|critical",
          "description": "string",
          "root_cause": "string",
          "suggested_fix": "string",
          "affected_prompt_component": "string|null"
        }}
      ],
      "summary": "string"
    }}
  ],
  "clusters": [
    {{
      "title": "string",
      "affected_run_ids": ["string"],
      "primary_category": "string",
      "severity": "low|medium|high|critical",
      "description": "string",
      "proposed_intervention": "string"
    }}
  ]
}}

Selected run data:
```json
{body}
```"""


def _compact_run_row(row: Any) -> dict[str, Any]:
    data = _row_to_dict(row)
    if data.get("prompt"):
        data["prompt"] = _compact_text(data["prompt"], 240)
    return data


def _compact_text(value: object, limit: int = 12000) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n\n[truncated: {len(text) - limit} chars omitted]"


def _compact_value(value: object, text_limit: int = 12000) -> object:
    if isinstance(value, str):
        return _compact_text(value, text_limit)
    if isinstance(value, list):
        items = [_compact_value(item, text_limit) for item in value[:200]]
        if len(value) > 200:
            items.append({"_truncated_items": len(value) - 200})
        return items
    if isinstance(value, dict):
        return {str(key): _compact_value(item, text_limit) for key, item in value.items()}
    return value


def _json_or_value(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(value)
