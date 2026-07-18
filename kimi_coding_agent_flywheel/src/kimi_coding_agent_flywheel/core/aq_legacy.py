"""Legacy JSON trace import for the Agent Quality database."""

from __future__ import annotations

from pathlib import Path

from ..timeutil import naive_utc_now


class AQLegacyImportMixin:
    """Import legacy JSON traces through the current ingestion APIs."""

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
                    completed_at=trace.end_time or naive_utc_now(),
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
