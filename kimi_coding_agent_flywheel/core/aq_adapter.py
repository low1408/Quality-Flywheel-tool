from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

import agent_quality.db as aq_db
import agent_quality.privacy.redaction as aq_redact
from agent_quality.capture.artifacts import write_artifact
from agent_quality.timeutil import utc_now

if TYPE_CHECKING:
    from .telemetry import Trace, TraceEvent
    from ..clustering.failure_analyzer import FailureCluster


class AQDbAdapter:
    """
    Adapter between kimi_coding_agent_flywheel and agent-quality SQLite.
    
    Exposes transactional APIs for telemetry ingestion, trace reconstruction,
    and failure analysis/clustering persistence. Enforces strict ingestion-time
    privacy redaction.
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        """Connect to the agent-quality SQLite database."""
        return aq_db.connect(self.db_path)

    def _redact_text(self, text: str | None) -> str:
        if not text:
            return ""
        return aq_redact.redact_text(text).value

    def _redact_dict(self, data: dict[str, Any] | None) -> dict[str, Any]:
        if not data:
            return {}
        return aq_redact.redact_json(data).value

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
                    "agent_version": "0.1.0",
                    "wrapper_version": "0.1.0",
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
                        "adapter_version": "0.1.0",
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
        from .telemetry import Trace, TraceEvent, EventType
        
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

    def save_analysis_run(
        self,
        analysis_id: str,
        algorithm: str,
        parameters: str | None = None,
        judge_version: str | None = None,
        redaction_version: str | None = None,
        status: str = "completed",
        selected_run_count: int = 0,
    ) -> None:
        """Insert or update a versioned failure analysis run log."""
        conn = self.connect()
        with conn:
            conn.execute(
                """
                INSERT INTO analysis_runs (
                    id, algorithm, parameters, judge_version, redaction_version, created_at,
                    status, selected_run_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    algorithm=excluded.algorithm,
                    parameters=excluded.parameters,
                    judge_version=excluded.judge_version,
                    redaction_version=excluded.redaction_version,
                    status=excluded.status,
                    selected_run_count=excluded.selected_run_count
                """,
                (
                    analysis_id,
                    algorithm,
                    parameters,
                    judge_version,
                    redaction_version,
                    utc_now(),
                    status,
                    selected_run_count,
                ),
            )

    def create_analysis_run(
        self,
        analysis_id: str,
        run_ids: list[str],
        *,
        parameters: dict[str, Any],
        judge_version: str,
    ) -> None:
        full_params = {
            "clustering_strategy": "root_cause_tfidf_v1",
            "feature_schema": "failure_features_v1",
            "algorithm": "dbscan",
            "metric": "cosine",
            "eps": 0.3,
            **parameters
        }
        self.save_analysis_run(
            analysis_id,
            "DBSCAN",
            parameters=json.dumps(full_params, sort_keys=True),
            judge_version=judge_version,
            redaction_version=aq_redact.POLICY_VERSION,
            status="running",
            selected_run_count=len(run_ids),
        )
        conn = self.connect()
        with conn:
            for run_id in run_ids:
                aq_db.insert(
                    conn,
                    "analysis_inputs",
                    {
                        "analysis_id": analysis_id,
                        "run_id": run_id,
                        "status": "pending",
                        "failure_count": 0,
                        "error_type": None,
                        "error_message": None,
                    },
                    or_action="OR REPLACE",
                )

    def update_analysis_input(
        self,
        analysis_id: str,
        run_id: str,
        *,
        status: str,
        failure_count: int = 0,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        conn = self.connect()
        with conn:
            conn.execute(
                """
                UPDATE analysis_inputs
                SET status=?, failure_count=?, error_type=?, error_message=?
                WHERE analysis_id=? AND run_id=?
                """,
                [status, failure_count, error_type, self._redact_text(error_message), analysis_id, run_id],
            )

    def finish_analysis_run(
        self,
        analysis_id: str,
        *,
        status: str,
        algorithm: str | None = None,
        failure_count: int = 0,
        cluster_count: int = 0,
        error_message: str | None = None,
    ) -> None:
        conn = self.connect()
        with conn:
            if algorithm:
                conn.execute(
                    """
                    UPDATE analysis_runs
                    SET status=?, completed_at=?, error_message=?, failure_count=?, cluster_count=?, algorithm=?
                    WHERE id=?
                    """,
                    [status, utc_now(), self._redact_text(error_message), failure_count, cluster_count, algorithm, analysis_id],
                )
            else:
                conn.execute(
                    """
                    UPDATE analysis_runs
                    SET status=?, completed_at=?, error_message=?, failure_count=?, cluster_count=?
                    WHERE id=?
                    """,
                    [status, utc_now(), self._redact_text(error_message), failure_count, cluster_count, analysis_id],
                )

    def save_failure_instance(
        self,
        conn: sqlite3.Connection,
        failure: Any,
        cluster_id: str | None = None,
        analysis_id: str | None = None,
    ) -> None:
        """Save a FailureInstance to the failure_instances SQLite table."""
        # Map timestamp to ISO format string safely
        ts_val = failure.timestamp.isoformat() if isinstance(failure.timestamp, datetime) else str(failure.timestamp)

        aq_db.insert(
            conn,
            "failure_instances",
            {
                "id": f"{analysis_id}:{failure.failure_id}" if analysis_id else failure.failure_id,
                "analysis_id": analysis_id,
                "run_id": failure.task_id,
                "cluster_id": cluster_id,
                "category": failure.category,
                "subcategory": failure.subcategory,
                "description": failure.description,
                "severity": failure.severity,
                "probable_cause": failure.probable_cause,
                "suggested_fix": failure.suggested_fix,
                "affected_prompt_component": failure.affected_prompt_component,
                "timestamp": ts_val,
                "llm_judge_score": failure.llm_judge_score,
            },
            or_action="OR REPLACE",
        )

    def save_failures(self, failures: list[Any], analysis_id: str | None = None) -> None:
        """Save a list of FailureInstance objects to SQLite."""
        conn = self.connect()
        with conn:
            for failure in failures:
                self.save_failure_instance(conn, failure, None, analysis_id)

    def persist_analysis_results(self, analysis_id: str, failures: list[Any], clusters: list[FailureCluster]) -> None:
        """Atomically persist all diagnosed failures, clusters, and memberships."""
        conn = self.connect()
        with conn:
            for failure in failures:
                self.save_failure_instance(conn, failure, None, analysis_id)
            self._save_clusters(conn, analysis_id, clusters)

    def save_clusters(self, analysis_id: str, clusters: list[FailureCluster]) -> None:
        """Persist FailureCluster definitions and map runs to cluster memberships in SQLite."""
        conn = self.connect()
        with conn:
            self._save_clusters(conn, analysis_id, clusters)

    def _save_clusters(self, conn: sqlite3.Connection, analysis_id: str, clusters: list[FailureCluster]) -> None:
        for cluster in clusters:
            # Stable cluster ID signature from dominant category, dominant subcategory, and affected prompt component
            sig_parts = [
                cluster.dominant_category or "unknown",
                cluster.dominant_subcategory or "unknown",
                cluster.failures[0].affected_prompt_component or "unknown" if cluster.failures else "unknown"
            ]
            sig = ":".join(sig_parts)
            import hashlib
            sig_hash = hashlib.sha256(sig.encode('utf-8')).hexdigest()[:12]
            cluster_id = f"cluster_{sig_hash}"

            existing = aq_db.one(conn, "SELECT id FROM failure_clusters WHERE id=?", [cluster_id])
            extra_payload = json.dumps(
                {
                    "dominant_subcategory": cluster.dominant_subcategory,
                    "common_keywords": cluster.common_keywords,
                    "common_tool_calls": cluster.common_tool_calls,
                    "regression_tests_needed": cluster.regression_tests_needed,
                }
            )

            if not existing:
                aq_db.insert(
                    conn,
                    "failure_clusters",
                    {
                        "id": cluster_id,
                        "title": cluster.label,
                        "description": cluster.description,
                        "primary_category": cluster.dominant_category,
                        "severity": cluster.avg_severity,
                        "status": "active",
                        "first_seen_at": utc_now(),
                        "last_seen_at": utc_now(),
                        "occurrence_count": len(cluster.failures),
                        "proposed_intervention": cluster.suggested_prompt_fix or cluster.suggested_tool_fix,
                        "linked_regression_case": None,
                        "provider_extensions": extra_payload,
                    },
                )
            else:
                conn.execute(
                    """
                    UPDATE failure_clusters SET
                        title=?, description=?, primary_category=?, severity=?,
                        proposed_intervention=?, provider_extensions=?, last_seen_at=?
                    WHERE id=?
                    """,
                    (
                        cluster.label,
                        cluster.description,
                        cluster.dominant_category,
                        cluster.avg_severity,
                        cluster.suggested_prompt_fix or cluster.suggested_tool_fix,
                        extra_payload,
                        utc_now(),
                        cluster_id,
                    ),
                )

            for failure in cluster.failures:
                self.save_failure_instance(conn, failure, cluster_id, analysis_id)
                confidence = getattr(failure, "cluster_confidence", None)
                if confidence is None:
                    confidence = float(failure.llm_judge_score or 10.0) / 10.0
                aq_db.insert(
                    conn,
                    "failure_cluster_memberships",
                    {
                        "analysis_id": analysis_id,
                        "run_id": failure.task_id,
                        "cluster_id": cluster_id,
                        "assignment_type": getattr(cluster, "assignment_type", "dbscan"),
                        "confidence": confidence,
                    },
                    or_action="OR REPLACE",
                )

            # Update count in a self-healing way
            conn.execute(
                """
                UPDATE failure_clusters
                SET occurrence_count=(SELECT COUNT(*) FROM failure_instances WHERE cluster_id=?)
                WHERE id=?
                """,
                [cluster_id, cluster_id]
            )

    def import_legacy_traces(self, directory: str | Path) -> int:
        """
        Scan a directory of legacy JSON traces, load them, and import them into SQLite
        with full sanitization/redaction. Returns the number of successfully imported traces.
        """
        from .telemetry import Trace
        
        dir_path = Path(directory)
        if not dir_path.exists():
            return 0
            
        imported_count = 0
        for trace_file in dir_path.glob("**/*.json"):
            try:
                trace = Trace.load(str(trace_file))
                session_id = trace.task_id or "default_session"
                self.save_session(
                    session_id=session_id,
                    repository_path=str(Path.cwd()),
                    started_at=trace.start_time,
                    task_summary=f"Imported legacy trace: {trace.trace_id}",
                )
                input_tokens = sum(e.tokens_in for e in trace.events if e.tokens_in)
                output_tokens = sum(e.tokens_out for e in trace.events if e.tokens_out)
                duration_ms = int(trace.duration_sec * 1000)
                
                self.save_run(
                    run_id=trace.trace_id,
                    session_id=session_id,
                    turn_number=1,
                    prompt=trace.system_prompt or "imported legacy task",
                    model=trace.model_id,
                    started_at=trace.start_time,
                    completed_at=trace.end_time or datetime.utcnow(),
                    duration_ms=duration_ms,
                    agent_status="completed" if not trace.has_errors else "failed",
                    verifier_status="passed" if not trace.has_errors else "failed",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
                self.save_events(
                    run_id=trace.trace_id,
                    session_id=session_id,
                    events=trace.events,
                )
                if trace.system_prompt:
                    self.save_artifact(
                        run_id=trace.trace_id,
                        artifact_type="prompt",
                        name="prompt.txt",
                        content=trace.system_prompt,
                    )
                imported_count += 1
            except Exception as e:
                import sys
                print(f"Warning: Failed to import legacy trace {trace_file}: {e}", file=sys.stderr)
                
        return imported_count
