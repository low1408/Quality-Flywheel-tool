"""Telemetry, artifact, and verifier-result ingestion into Agent Quality."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import agent_quality.db as aq_db
import agent_quality.privacy.redaction as aq_redact
from agent_quality.capture.artifacts import write_artifact
from agent_quality.timeutil import utc_now
from kimi_coding_agent_flywheel import __version__

from .telemetry_models import TraceEvent


class AQIngestionMixin:
    """Persist sessions, runs, events, artifacts, and verifier results."""

    def save_session(
        self,
        session_id: str,
        repository_path: str,
        started_at: datetime | str,
        task_summary: str,
    ) -> None:
        """Create or update a session in the database."""
        started_str = started_at.isoformat() if isinstance(started_at, datetime) else started_at
        conn = self.connect()
        with conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO sessions (
                    id, repository_path, repository_remote_hash, started_at, ended_at, final_outcome, task_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    str(repository_path),
                    None,
                    started_str,
                    None,
                    None,
                    self._redact_text(task_summary)[:240],
                ),
            )

    def save_run(
        self,
        run_id: str,
        session_id: str,
        turn_number: int,
        prompt: str,
        model: str | None,
        started_at: datetime | str,
        completed_at: datetime | str | None = None,
        duration_ms: int | None = None,
        agent_status: str = "completed",
        verifier_status: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Save run metadata to the runs table."""
        started_str = started_at.isoformat() if isinstance(started_at, datetime) else started_at
        completed_str = (
            (completed_at.isoformat() if isinstance(completed_at, datetime) else completed_at)
            if completed_at
            else None
        )
        
        redacted_prompt = self._redact_text(prompt)
        prompt_hash = aq_redact.redact_text(prompt).value # fallback or hashutil
        
        # Calculate prompt_hash via hashlib to ensure stable hash
        import hashlib
        prompt_hash = hashlib.sha256(redacted_prompt.encode("utf-8")).hexdigest()

        conn = self.connect()
        with conn:
            aq_db.insert(
                conn,
                "runs",
                {
                    "id": run_id,
                    "session_id": session_id,
                    "turn_number": turn_number,
                    "prompt": redacted_prompt,
                    "prompt_hash": prompt_hash,
                    "repository_path": str(Path.cwd()),
                    "base_commit": "unknown",
                    "resulting_commit": None,
                    "model": model,
                    "agent_adapter": "kimi",
                    "agent_version": __version__,
                    "wrapper_version": __version__,
                    "codex_config_hash": None,
                    "agents_md_hash": None,
                    "verifier_version": "1.0",
                    "started_at": started_str,
                    "completed_at": completed_str,
                    "duration_ms": duration_ms,
                    "agent_status": agent_status,
                    "verifier_status": verifier_status,
                    "human_status": "not_reviewed",
                    "lifecycle_status": "closed" if completed_str else "still_open",
                    "input_tokens": input_tokens,
                    "cached_input_tokens": None,
                    "output_tokens": output_tokens,
                },
            )

    def save_events(self, run_id: str, session_id: str, events: list[TraceEvent]) -> None:
        """Ingest trace events with full privacy sanitization under transaction isolation."""
        conn = self.connect()
        with conn:
            for seq, ev in enumerate(events, start=1):
                raw_payload = ev.to_dict()
                
                # Apply authoritative ingestion-time redaction
                redacted_payload = self._redact_dict(raw_payload)
                redacted_content = self._redact_text(ev.content)
                redacted_tool_error = self._redact_text(ev.tool_error) if ev.tool_error else None

                # Determine status
                status = "completed"
                if ev.event_type.name == "ERROR":
                    status = "failed"
                elif ev.tool_error:
                    status = "failed"

                # Map classification category
                item_type = None
                tool_category = None
                if "LLM" in ev.event_type.name:
                    item_type = "assistant_output"
                elif "TOOL" in ev.event_type.name:
                    item_type = "command_execution"
                    tool_category = "shell"
                    if ev.tool_name and any(p in ev.tool_name.lower() for p in ("edit", "write", "patch")):
                        tool_category = "file_edit"
                    elif ev.tool_name and "git" in ev.tool_name.lower():
                        tool_category = "vcs"

                event_id = ev.event_id or f"evt_{uuid.uuid4().hex[:8]}"

                # Serialize metadata and extensions safely
                normalized_payload = json.dumps({
                    "content": redacted_content,
                    "latency_ms": ev.latency_ms,
                    "model": ev.model,
                    "tokens_in": ev.tokens_in,
                    "tokens_out": ev.tokens_out,
                })
                
                provider_extensions = json.dumps({
                    "decision_options": redacted_payload.get("decision_options"),
                    "decision_choice": redacted_payload.get("decision_choice"),
                    "decision_reasoning": redacted_payload.get("decision_reasoning"),
                    "tool_input": redacted_payload.get("tool_input"),
                    "tool_output": redacted_payload.get("tool_output"),
                })

                aq_db.insert(
                    conn,
                    "events",
                    {
                        "id": event_id,
                        "schema_version": "1.0",
                        "event_type": f"agent.{ev.event_type.name.lower()}",
                        "source_provider": "kimi",
                        "source_product": "flywheel",
                        "source_event_type": ev.event_type.name,
                        "adapter_version": __version__,
                        "session_id": session_id,
                        "run_id": run_id,
                        "turn_id": None,
                        "parent_event_id": ev.parent_event_id,
                        "sequence_number": seq,
                        "occurred_at": ev.timestamp.isoformat(),
                        "observed_at": utc_now(),
                        "status": status,
                        "item_type": item_type,
                        "tool_category": tool_category,
                        "command": ev.tool_name,
                        "exit_code": 1 if redacted_tool_error else 0,
                        "path": None,
                        "duration_ms": int(ev.latency_ms),
                        "normalized_payload": normalized_payload,
                        "source_payload_sanitized": json.dumps(redacted_payload),
                        "provider_extensions": provider_extensions,
                        "privacy_status": "sanitized",
                        "privacy_policy_version": aq_redact.POLICY_VERSION,
                        "redaction_findings": json.dumps([]),
                        "normalization_status": "normalized",
                        "idempotency_key": f"{run_id}_{seq}_{event_id}",
                    },
                )

    def save_artifact(self, run_id: str, artifact_type: str, name: str, content: str) -> None:
        """Write artifact content to disk and log to the artifacts table."""
        # Sanitize/redact artifact content prior to save
        redacted_content = self._redact_text(content)
        
        artifact_id, path, digest, size = write_artifact(run_id, name, redacted_content)
        conn = self.connect()
        with conn:
            aq_db.insert(
                conn,
                "artifacts",
                {
                    "id": artifact_id,
                    "run_id": run_id,
                    "artifact_type": artifact_type,
                    "path": str(path),
                    "sha256": digest,
                    "size_bytes": size,
                },
            )

    def save_verifier_result(
        self,
        run_id: str,
        verifier_name: str,
        category: str,
        passed: bool,
        exit_code: int = 0,
        duration_ms: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        """Save verifier run result to the verifier_results table."""
        conn = self.connect()
        
        # Redact stdout/stderr and write as artifacts first
        stdout_id = f"stdout_{run_id}_{verifier_name}.txt"
        stderr_id = f"stderr_{run_id}_{verifier_name}.txt"
        
        self.save_artifact(run_id, "verifier_log", stdout_id, stdout)
        self.save_artifact(run_id, "verifier_log", stderr_id, stderr)

        # Look up artifacts to get paths
        with conn:
            stdout_row = aq_db.one(conn, "SELECT path FROM artifacts WHERE run_id=? AND path LIKE ?", [run_id, f"%{stdout_id}%"])
            stderr_row = aq_db.one(conn, "SELECT path FROM artifacts WHERE run_id=? AND path LIKE ?", [run_id, f"%{stderr_id}%"])
            
            stdout_path = stdout_row["path"] if stdout_row else None
            stderr_path = stderr_row["path"] if stderr_row else None

            aq_db.insert(
                conn,
                "verifier_results",
                {
                    "id": f"ver_{uuid.uuid4().hex[:8]}",
                    "run_id": run_id,
                    "verifier_name": verifier_name,
                    "verifier_category": category,
                    "command": None,
                    "started_at": utc_now(),
                    "duration_ms": duration_ms,
                    "exit_code": exit_code,
                    "passed": 1 if passed else 0,
                    "stdout_path": stdout_path,
                    "stderr_path": stderr_path,
                },
            )
