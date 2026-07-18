"""Live trace recording and persistence coordination."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from ..timeutil import naive_utc_now
from .telemetry_models import EventType, Trace, TraceEvent

current_trace: ContextVar[Trace | None] = ContextVar("current_trace", default=None)

class Tracer:
    """
    High-level tracer for instrumenting agent execution.

    Provides a convenient context-manager-based API for recording traces.
    """

    def __init__(self, agent_name: str, model_id: str | None = None, output_dir: str = "data/traces", db_path: str | None = None):
        self.agent_name = agent_name
        self.model_id = model_id
        self.output_dir = Path(output_dir)
        self._current_trace: Trace | None = None
        self._current_span_stack: list[str] = []
        
        # SQLite Database Ingestion Adapter
        from .aq_adapter import AQDbAdapter
        self.db_adapter = AQDbAdapter(db_path)

    @contextmanager
    def trace(self, task_id: str | None = None, **metadata: Any) -> Iterator[Trace]:
        """Start a new trace context."""
        trace = Trace(
            trace_id=str(uuid.uuid4())[:8],
            agent_name=self.agent_name,
            model_id=self.model_id,
            task_id=task_id,
        )

        # Set trace metadata
        trace.model_params = metadata.get("model_params", {})
        trace.system_prompt = metadata.get("system_prompt")
        trace.tool_definitions = metadata.get("tool_definitions", [])

        self._current_trace = trace
        self._current_span_stack = []
        token = current_trace.set(trace)

        try:
            yield trace
        finally:
            trace.finalize()
            self._save_trace(trace)
            current_trace.reset(token)
            self._current_trace = None
            self._current_span_stack = []

    @contextmanager
    def span(self, name: str, **metadata: Any) -> Iterator[str]:
        """Start a new span within the current trace."""
        trace = self._current_trace
        if not trace:
            yield ""  # No-op if no active trace
            return

        parent_id = self._current_span_stack[-1] if self._current_span_stack else None
        span_id = trace.start_span(name, parent_span_id=parent_id, **metadata)
        self._current_span_stack.append(span_id)

        try:
            yield span_id
        finally:
            trace.end_span(span_id)
            if self._current_span_stack and self._current_span_stack[-1] == span_id:
                self._current_span_stack.pop()

    def record_llm_call(
        self,
        messages: list[dict[str, Any]],
        response: str,
        model: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: float = 0.0,
        **metadata: Any,
) -> None:
        """Record an LLM call event."""
        trace = self._current_trace
        if not trace:
            return

        event = TraceEvent(
            event_id=str(uuid.uuid4())[:8],
            event_type=EventType.LLM_REQUEST,
            timestamp=naive_utc_now(),
            step_number=0,  # Will be set by add_event
            model=model or self.model_id,
            messages=messages,
            response=response,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            metadata=metadata,
            span_id=self._current_span_stack[-1] if self._current_span_stack else None,
        )
        trace.add_event(event)

    def record_tool_call(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_output: Any | None = None,
        tool_error: str | None = None,
        latency_ms: float = 0.0,
        **metadata: Any,
    ) -> None:
        """Record a tool call event."""
        trace = self._current_trace
        if not trace:
            return

        event = TraceEvent(
            event_id=str(uuid.uuid4())[:8],
            event_type=EventType.TOOL_CALL,
            timestamp=naive_utc_now(),
            step_number=0,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            tool_error=tool_error,
            latency_ms=latency_ms,
            metadata=metadata,
            span_id=self._current_span_stack[-1] if self._current_span_stack else None,
        )
        trace.add_event(event)

    def record_thought(self, thought: str, **metadata: Any) -> None:
        """Record an agent's reasoning/thought."""
        trace = self._current_trace
        if not trace:
            return

        event = TraceEvent(
            event_id=str(uuid.uuid4())[:8],
            event_type=EventType.THOUGHT,
            timestamp=naive_utc_now(),
            step_number=0,
            content=thought,
            metadata=metadata,
            span_id=self._current_span_stack[-1] if self._current_span_stack else None,
        )
        trace.add_event(event)

    def record_decision(
        self,
        decision: str,
        options: list[str] | None = None,
        reasoning: str | None = None,
        **metadata: Any,
    ) -> None:
        """Record a decision point."""
        trace = self._current_trace
        if not trace:
            return

        event = TraceEvent(
            event_id=str(uuid.uuid4())[:8],
            event_type=EventType.DECISION,
            timestamp=naive_utc_now(),
            step_number=0,
            content=decision,
            decision_options=options,
            decision_choice=decision,
            decision_reasoning=reasoning or "",
            metadata=metadata,
            span_id=self._current_span_stack[-1] if self._current_span_stack else None,
        )
        trace.add_event(event)

    def record_error(self, error_message: str, exception: Exception | None = None, **metadata: Any) -> None:
        """Record an error event."""
        trace = self._current_trace
        if not trace:
            return

        error_content = error_message
        if exception:
            error_content += f"\nException: {type(exception).__name__}: {str(exception)}"

        event = TraceEvent(
            event_id=str(uuid.uuid4())[:8],
            event_type=EventType.ERROR,
            timestamp=naive_utc_now(),
            step_number=0,
            content=error_content,
            metadata=metadata,
            span_id=self._current_span_stack[-1] if self._current_span_stack else None,
        )
        trace.add_event(event)

    def record_state_change(self, key: str, old_value: Any, new_value: Any, **metadata: Any) -> None:
        """Record a state change."""
        trace = self._current_trace
        if not trace:
            return

        event = TraceEvent(
            event_id=str(uuid.uuid4())[:8],
            event_type=EventType.STATE_CHANGE,
            timestamp=naive_utc_now(),
            step_number=0,
            content=f"{key}: {old_value} -> {new_value}",
            metadata={"key": key, "old": old_value, "new": new_value, **metadata},
            span_id=self._current_span_stack[-1] if self._current_span_stack else None,
        )
        trace.add_event(event)

    def record_metric(self, name: str, value: float, unit: str = "", **metadata: Any) -> None:
        """Record a custom metric."""
        trace = self._current_trace
        if not trace:
            return

        event = TraceEvent(
            event_id=str(uuid.uuid4())[:8],
            event_type=EventType.METRIC,
            timestamp=naive_utc_now(),
            step_number=0,
            content=f"{name}: {value} {unit}",
            metadata={"metric_name": name, "value": value, "unit": unit, **metadata},
            span_id=self._current_span_stack[-1] if self._current_span_stack else None,
        )
        trace.add_event(event)

    def _save_trace(self, trace: Trace) -> None:
        """Persist trace to database and fallback to disk."""
        try:
            session_id = trace.task_id or "default_session"
            # Ensure session exists
            self.db_adapter.save_session(
                session_id=session_id,
                repository_path=str(Path.cwd()),
                started_at=trace.start_time,
                task_summary=f"Kimi benchmark task: {trace.trace_id}",
            )
            
            # Count tokens
            input_tokens = sum(e.tokens_in for e in trace.events if e.tokens_in)
            output_tokens = sum(e.tokens_out for e in trace.events if e.tokens_out)
            duration_ms = int(trace.duration_sec * 1000)
            
            # Save run record
            self.db_adapter.save_run(
                run_id=trace.trace_id,
                session_id=session_id,
                turn_number=1,
                prompt=trace.system_prompt or "kimi evaluation task",
                model=trace.model_id,
                started_at=trace.start_time,
                completed_at=trace.end_time or naive_utc_now(),
                duration_ms=duration_ms,
                agent_status="completed" if not trace.has_errors else "failed",
                verifier_status="passed" if not trace.has_errors else "failed",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            
            # Save redacted events
            self.db_adapter.save_events(
                run_id=trace.trace_id,
                session_id=session_id,
                events=trace.events,
            )
            
            # Save prompt artifact
            if trace.system_prompt:
                self.db_adapter.save_artifact(
                    run_id=trace.trace_id,
                    artifact_type="prompt",
                    name="prompt.txt",
                    content=trace.system_prompt,
                )
        except Exception as e:
            import sys
            print(f"Warning: Failed to save trace to SQLite: {e}", file=sys.stderr)

        # Fallback JSON serialization for local caching/compatibility
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            trace.save(str(self.output_dir))
        except Exception:
            pass
