"""Projection of Agent Quality rows into canonical worker traces."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import agent_quality.db as aq_db

from .telemetry_models import Trace


class AQTraceProjectionMixin:
    """Load canonical trace dictionaries and in-memory trace objects."""

    def load_run_traces(self, run_ids: list[str]) -> list[dict[str, Any]]:
        """Load exact Agent Quality runs as canonical, redacted trace dictionaries."""
        if not run_ids:
            return []
        unique_ids = list(dict.fromkeys(str(run_id) for run_id in run_ids))
        placeholders = ",".join("?" for _ in unique_ids)
        conn = self.connect()
        rows = aq_db.all_rows(conn, f"SELECT * FROM runs WHERE id IN ({placeholders})", unique_ids)
        by_id = {row["id"]: row for row in rows}
        missing = [run_id for run_id in unique_ids if run_id not in by_id]
        if missing:
            raise ValueError(f"unknown run IDs: {', '.join(missing)}")

        traces: list[dict[str, Any]] = []
        for run_id in unique_ids:
            run = by_id[run_id]
            events = aq_db.all_rows(
                conn,
                "SELECT * FROM events WHERE run_id=? ORDER BY COALESCE(sequence_number, rowid), rowid",
                [run_id],
            )
            prompt_artifact = aq_db.one(
                conn,
                "SELECT path FROM artifacts WHERE run_id=? AND artifact_type='prompt' ORDER BY rowid DESC LIMIT 1",
                [run_id],
            )
            system_prompt = None
            if prompt_artifact:
                artifact_path = Path(prompt_artifact["path"])
                if artifact_path.is_file():
                    system_prompt = artifact_path.read_text(encoding="utf-8", errors="replace")
            traces.append(
                {
                    "trace_id": run_id,
                    "task_id": run_id,
                    "task_description": run["prompt"] or "",
                    "agent_name": run["agent_adapter"],
                    "model_id": run["model"],
                    "system_prompt": system_prompt,
                    "start_time": run["started_at"],
                    "end_time": run["completed_at"],
                    "events": [self._canonical_event(row) for row in events],
                }
            )
        conn.close()
        return traces

    def _canonical_event(self, row: sqlite3.Row) -> dict[str, Any]:
        normalized = self._json_object(row["normalized_payload"])
        source = self._json_object(row["source_payload_sanitized"])
        extensions = self._json_object(row["provider_extensions"])
        hook = self._nested_hook(source) or self._extension_hook(extensions)
        source_type = str(row["source_event_type"] or "")
        event_type = self._canonical_event_type(row, source_type)
        content = self._first_text(
            normalized.get("content"),
            normalized.get("assistant_output"),
            normalized.get("reasoning"),
            normalized.get("output"),
            source.get("content"),
            source.get("text"),
            source.get("message"),
            source.get("output"),
            hook.get("last_assistant_message"),
            hook.get("assistant_message"),
            hook.get("response"),
            hook.get("prompt") if source_type == "UserPromptSubmit" else None,
        )
        tool_error = self._first_text(
            source.get("tool_error"),
            normalized.get("tool_error"),
            normalized.get("error"),
            hook.get("tool_error"),
            hook.get("error"),
        )
        if not tool_error and (row["status"] == "failed" or (row["exit_code"] not in (None, 0))):
            tool_error = content or f"{source_type or 'event'} failed"
        return {
            "event_id": row["id"],
            "event_type": event_type,
            "timestamp": row["occurred_at"] or row["observed_at"],
            "step_number": row["sequence_number"] or 0,
            "content": content,
            "metadata": {},
            "model": normalized.get("model") or source.get("model") or hook.get("model"),
            "tokens_in": normalized.get("tokens_in") or source.get("tokens_in") or 0,
            "tokens_out": normalized.get("tokens_out") or source.get("tokens_out") or 0,
            "latency_ms": normalized.get("latency_ms") or row["duration_ms"] or 0,
            "tool_name": row["command"] or normalized.get("tool_name") or normalized.get("tool") or source.get("tool_name") or hook.get("tool_name"),
            "tool_input": normalized.get("tool_input") or extensions.get("tool_input") or source.get("tool_input") or hook.get("tool_input") or hook.get("arguments"),
            "tool_output": normalized.get("tool_output") or extensions.get("tool_output") or source.get("tool_output") or hook.get("tool_output"),
            "tool_error": tool_error or None,
            "decision_options": extensions.get("decision_options"),
            "decision_choice": extensions.get("decision_choice"),
            "decision_reasoning": extensions.get("decision_reasoning"),
            "parent_event_id": row["parent_event_id"],
        }

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if not value:
            return {}
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _nested_hook(source: dict[str, Any]) -> dict[str, Any]:
        extensions = source.get("extensions")
        if not isinstance(extensions, dict):
            return {}
        hook = extensions.get("openai.codex.hook")
        return hook if isinstance(hook, dict) else {}

    @staticmethod
    def _extension_hook(extensions: dict[str, Any]) -> dict[str, Any]:
        hook = extensions.get("openai.codex.hook")
        return hook if isinstance(hook, dict) else {}

    @staticmethod
    def _first_text(*values: Any) -> str:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (dict, list)):
                return json.dumps(value, sort_keys=True)
        return ""

    @staticmethod
    def _canonical_event_type(row: sqlite3.Row, source_type: str) -> str:
        known = {
            "LLM_REQUEST", "LLM_RESPONSE", "TOOL_CALL", "TOOL_RESULT", "THOUGHT",
            "ACTION", "OBSERVATION", "DECISION", "STATE_CHANGE", "ERROR", "COMPLETION", "METRIC",
        }
        upper = source_type.upper()
        if upper in known:
            return upper
        event_name = str(row["event_type"] or "").lower()
        if row["status"] == "failed" or (row["exit_code"] not in (None, 0)) or "error" in event_name:
            return "ERROR"
        if row["command"] or row["tool_category"] or "tool" in event_name:
            return "TOOL_RESULT" if any(token in event_name for token in ("completed", "result", "after")) else "TOOL_CALL"
        if row["item_type"] == "assistant_output" or any(token in event_name for token in ("message", "response")):
            return "LLM_RESPONSE"
        if any(token in event_name for token in ("stop", "complete")):
            return "COMPLETION"
        return "OBSERVATION"

    def load_session_traces(self, session_id: str | list[str]) -> list[Trace]:
        """Reconstruct Kimi Trace objects from authoritative SQLite storage."""
        from .telemetry_models import EventType, TraceEvent
        
        session_ids = [session_id] if isinstance(session_id, str) else session_id
        if not session_ids:
            return []

        placeholders = ",".join("?" for _ in session_ids)
        conn = self.connect()
        with conn:
            run_rows = aq_db.all_rows(
                conn,
                f"SELECT * FROM runs WHERE session_id IN ({placeholders}) AND lifecycle_status='closed'",
                session_ids
            )
            traces = []
            
            for run_row in run_rows:
                run_id = run_row["id"]
                trace = Trace(
                    trace_id=run_id,
                    agent_name=run_row["agent_adapter"],
                    model_id=run_row["model"],
                    task_id=run_id,
                    start_time=datetime.fromisoformat(run_row["started_at"]),
                    end_time=datetime.fromisoformat(run_row["completed_at"]) if run_row["completed_at"] else None,
                    system_prompt=None, # Loaded from prompt artifact below
                    total_tokens=int(run_row["input_tokens"] or 0) + int(run_row["output_tokens"] or 0),
                )

                # Load prompt artifact if present
                prompt_row = aq_db.one(conn, "SELECT path FROM artifacts WHERE run_id=? AND artifact_type='prompt'", [run_id])
                if prompt_row and Path(prompt_row["path"]).exists():
                    trace.system_prompt = Path(prompt_row["path"]).read_text(encoding="utf-8")

                # Load and reconstruct events
                event_rows = aq_db.all_rows(
                    conn,
                    "SELECT * FROM events WHERE run_id=? ORDER BY COALESCE(sequence_number, rowid), rowid",
                    [run_id]
                )
                
                for ev_row in event_rows:
                    # Map back source payloads
                    ext_data = {}
                    if ev_row["provider_extensions"]:
                        try:
                            ext_data = json.loads(ev_row["provider_extensions"])
                        except Exception:
                            pass
                            
                    source_payload = {}
                    if ev_row["source_payload_sanitized"]:
                        try:
                            source_payload = json.loads(ev_row["source_payload_sanitized"])
                        except Exception:
                            pass

                    # Extract event type mapping
                    event_type_name = ev_row["source_event_type"]
                    try:
                        etype = EventType[event_type_name]
                    except KeyError:
                        etype = EventType.THOUGHT

                    event = TraceEvent(
                        event_id=ev_row["id"],
                        event_type=etype,
                        timestamp=datetime.fromisoformat(ev_row["occurred_at"]),
                        step_number=ev_row["sequence_number"] or 0,
                        content=source_payload.get("content", ""),
                        metadata=source_payload.get("metadata", {}),
                        model=source_payload.get("model"),
                        messages=source_payload.get("messages"),
                        response=source_payload.get("response"),
                        tokens_in=source_payload.get("tokens_in", 0),
                        tokens_out=source_payload.get("tokens_out", 0),
                        latency_ms=float(ev_row["duration_ms"] or 0.0),
                        tool_name=ev_row["command"],
                        tool_input=ext_data.get("tool_input"),
                        tool_output=ext_data.get("tool_output"),
                        tool_error=source_payload.get("tool_error"),
                        decision_options=ext_data.get("decision_options"),
                        decision_choice=ext_data.get("decision_choice"),
                        decision_reasoning=ext_data.get("decision_reasoning"),
                        parent_event_id=ev_row["parent_event_id"],
                        span_id=source_payload.get("span_id"),
                    )
                    trace.events.append(event)
                
                traces.append(trace)
            
            return traces
