"""
Telemetry and Instrumentation for Coding Agent Quality Flywheel.

Captures detailed execution traces, tool calls, and agent outputs
for later analysis, clustering, and regression testing.

Inspired by AgentTrace, Langfuse, and agent-replay patterns.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

import numpy as np


# Thread-local trace context
current_trace: ContextVar[Trace | None] = ContextVar("current_trace", default=None)


class EventType(Enum):
    """Types of events in an agent trace."""
    LLM_REQUEST = auto()
    LLM_RESPONSE = auto()
    TOOL_CALL = auto()
    TOOL_RESULT = auto()
    THOUGHT = auto()
    ACTION = auto()
    OBSERVATION = auto()
    DECISION = auto()
    STATE_CHANGE = auto()
    ERROR = auto()
    COMPLETION = auto()
    METRIC = auto()


@dataclass
class TraceEvent:
    """A single event in an agent execution trace."""
    event_id: str
    event_type: EventType
    timestamp: datetime
    step_number: int

    # Content
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    # For LLM events
    model: str | None = None
    messages: list[dict[str, Any]] | None = None
    response: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0

    # For tool events
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: Any | None = None
    tool_error: str | None = None

    # For decision events
    decision_options: list[str] | None = None
    decision_choice: str | None = None
    decision_reasoning: str | None = None

    # Parent-child relationships
    parent_event_id: str | None = None
    span_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.name,
            "timestamp": self.timestamp.isoformat(),
            "step_number": self.step_number,
            "content": self.content[:1000] if len(self.content) > 1000 else self.content,
            "metadata": self.metadata,
            "model": self.model,
            "messages": self.messages,
            "response": self.response,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "latency_ms": self.latency_ms,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "tool_output": self.tool_output,
            "tool_error": self.tool_error,
            "decision_options": self.decision_options,
            "decision_choice": self.decision_choice,
            "decision_reasoning": self.decision_reasoning,
            "parent_event_id": self.parent_event_id,
            "span_id": self.span_id,
        }


@dataclass
class TraceSpan:
    """A span represents a logical grouping of events (e.g., a planning phase)."""
    span_id: str
    name: str
    start_time: datetime
    end_time: datetime | None = None
    parent_span_id: str | None = None
    events: list[TraceEvent] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds() * 1000
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "name": self.name,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "parent_span_id": self.parent_span_id,
            "duration_ms": self.duration_ms,
            "events": [e.to_dict() for e in self.events],
            "metadata": self.metadata,
        }


@dataclass
class Trace:
    """
    Complete execution trace for a single agent task.

    This is the primary data structure for capturing everything an agent did,
    enabling replay, analysis, and regression testing.
    """
    trace_id: str
    agent_name: str
    model_id: str | None = None
    task_id: str | None = None

    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: datetime | None = None

    events: list[TraceEvent] = field(default_factory=list)
    spans: dict[str, TraceSpan] = field(default_factory=dict)

    # Configuration at time of execution
    system_prompt: str | None = None
    model_params: dict[str, Any] = field(default_factory=dict)
    tool_definitions: list[dict[str, Any]] = field(default_factory=list)

    # Totals
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_tool_calls: int = 0
    total_llm_calls: int = 0

    # Open spans (for tracking during recording)
    _open_spans: dict[str, TraceSpan] = field(default_factory=dict, repr=False)
    _step_counter: int = 0

    @property
    def duration_sec(self) -> float:
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return (datetime.utcnow() - self.start_time).total_seconds()

    @property
    def event_counts(self) -> dict[str, int]:
        """Count events by type."""
        counts: dict[str, int] = defaultdict(int)
        for e in self.events:
            counts[e.event_type.name] += 1
        return dict(counts)

    @property
    def tool_call_summary(self) -> dict[str, int]:
        """Summary of which tools were called and how often."""
        summary: dict[str, int] = defaultdict(int)
        for e in self.events:
            if e.event_type == EventType.TOOL_CALL and e.tool_name:
                summary[e.tool_name] += 1
        return dict(summary)

    @property
    def error_count(self) -> int:
        return sum(1 for e in self.events if e.event_type == EventType.ERROR)

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0

    def add_event(self, event: TraceEvent) -> None:
        """Add an event to the trace."""
        event.step_number = self._step_counter
        self._step_counter += 1
        self.events.append(event)

        # Update counters
        if event.event_type == EventType.LLM_REQUEST:
            self.total_llm_calls += 1
            self.total_tokens += event.tokens_in + event.tokens_out
        elif event.event_type == EventType.TOOL_CALL:
            self.total_tool_calls += 1

        # Add to current span if any
        if event.span_id and event.span_id in self._open_spans:
            self._open_spans[event.span_id].events.append(event)

    def start_span(self, name: str, span_id: str | None = None, parent_span_id: str | None = None, **metadata: Any) -> str:
        """Start a new span."""
        sid = span_id or str(uuid.uuid4())[:8]
        span = TraceSpan(
            span_id=sid,
            name=name,
            start_time=datetime.utcnow(),
            parent_span_id=parent_span_id,
            metadata=metadata,
        )
        self._open_spans[sid] = span
        self.spans[sid] = span
        return sid

    def end_span(self, span_id: str) -> None:
        """End an open span."""
        if span_id in self._open_spans:
            self._open_spans[span_id].end_time = datetime.utcnow()
            del self._open_spans[span_id]

    def finalize(self) -> None:
        """Finalize the trace - close any open spans and set end time."""
        # Close all open spans
        for span_id in list(self._open_spans.keys()):
            self.end_span(span_id)
        self.end_time = datetime.utcnow()

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "agent_name": self.agent_name,
            "model_id": self.model_id,
            "task_id": self.task_id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_sec": self.duration_sec,
            "events": [e.to_dict() for e in self.events],
            "spans": {k: v.to_dict() for k, v in self.spans.items()},
            "system_prompt": self.system_prompt,
            "model_params": self.model_params,
            "tool_definitions": self.tool_definitions,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "total_tool_calls": self.total_tool_calls,
            "total_llm_calls": self.total_llm_calls,
            "event_counts": self.event_counts,
            "tool_call_summary": self.tool_call_summary,
            "error_count": self.error_count,
        }

    def save(self, directory: str = "data/traces") -> Path:
        """Save trace to disk as JSON."""
        dir_path = Path(directory)
        dir_path.mkdir(parents=True, exist_ok=True)

        filename = f"{self.trace_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = dir_path / filename

        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)

        return filepath

    @classmethod
    def load(cls, filepath: str) -> Trace:
        """Load a trace from disk."""
        with open(filepath) as f:
            data = json.load(f)

        trace = cls(
            trace_id=data["trace_id"],
            agent_name=data["agent_name"],
            model_id=data.get("model_id"),
            task_id=data.get("task_id"),
            start_time=datetime.fromisoformat(data["start_time"]),
            end_time=datetime.fromisoformat(data["end_time"]) if data.get("end_time") else None,
            system_prompt=data.get("system_prompt"),
            model_params=data.get("model_params", {}),
            tool_definitions=data.get("tool_definitions", []),
            total_tokens=data.get("total_tokens", 0),
            total_cost_usd=data.get("total_cost_usd", 0.0),
            total_tool_calls=data.get("total_tool_calls", 0),
            total_llm_calls=data.get("total_llm_calls", 0),
        )

        # Reconstruct events
        for e_data in data.get("events", []):
            event = TraceEvent(
                event_id=e_data["event_id"],
                event_type=EventType[e_data["event_type"]],
                timestamp=datetime.fromisoformat(e_data["timestamp"]),
                step_number=e_data["step_number"],
                content=e_data.get("content", ""),
                metadata=e_data.get("metadata", {}),
                model=e_data.get("model"),
                messages=e_data.get("messages"),
                response=e_data.get("response"),
                tokens_in=e_data.get("tokens_in", 0),
                tokens_out=e_data.get("tokens_out", 0),
                latency_ms=e_data.get("latency_ms", 0.0),
                tool_name=e_data.get("tool_name"),
                tool_input=e_data.get("tool_input"),
                tool_output=e_data.get("tool_output"),
                tool_error=e_data.get("tool_error"),
                decision_options=e_data.get("decision_options"),
                decision_choice=e_data.get("decision_choice"),
                decision_reasoning=e_data.get("decision_reasoning"),
                parent_event_id=e_data.get("parent_event_id"),
                span_id=e_data.get("span_id"),
            )
            trace.events.append(event)

        return trace


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
        from core.aq_adapter import AQDbAdapter
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
            timestamp=datetime.utcnow(),
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
            timestamp=datetime.utcnow(),
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
            timestamp=datetime.utcnow(),
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
            timestamp=datetime.utcnow(),
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
            timestamp=datetime.utcnow(),
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
            timestamp=datetime.utcnow(),
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
            timestamp=datetime.utcnow(),
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
                completed_at=trace.end_time or datetime.utcnow(),
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


# -----------------------------------------------------------------------------
# Decorator-based instrumentation helpers
# -----------------------------------------------------------------------------

def instrument_llm_call(
    tracer: Tracer,
    model_attr: str = "model",
):
    """Decorator to instrument LLM API calls."""
    def decorator(func: Callable) -> Callable:
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.time()
            try:
                result = await func(*args, **kwargs)
                latency = (time.time() - start) * 1000

                # Extract messages from common patterns
                messages = kwargs.get("messages", args[1] if len(args) > 1 else [])
                if not isinstance(messages, list):
                    messages = []

                tracer.record_llm_call(
                    messages=messages,
                    response=str(result)[:2000],
                    model=kwargs.get(model_attr),
                    latency_ms=latency,
                )
                return result
            except Exception as e:
                latency = (time.time() - start) * 1000
                tracer.record_error(f"LLM call failed after {latency:.0f}ms", exception=e)
                raise

        return async_wrapper
    return decorator


def instrument_tool_call(tracer: Tracer):
    """Decorator to instrument tool function calls."""
    def decorator(func: Callable) -> Callable:
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.time()
            tool_name = func.__name__
            try:
                result = await func(*args, **kwargs)
                latency = (time.time() - start) * 1000
                tracer.record_tool_call(
                    tool_name=tool_name,
                    tool_input=kwargs,
                    tool_output=result,
                    latency_ms=latency,
                )
                return result
            except Exception as e:
                latency = (time.time() - start) * 1000
                tracer.record_tool_call(
                    tool_name=tool_name,
                    tool_input=kwargs,
                    tool_error=str(e),
                    latency_ms=latency,
                )
                raise

        return async_wrapper
    return decorator


# -----------------------------------------------------------------------------
# Trace Analysis Utilities
# -----------------------------------------------------------------------------

class TraceAnalyzer:
    """Utilities for analyzing collections of traces."""

    def __init__(self, traces: list[Trace]):
        self.traces = traces

    def get_error_traces(self) -> list[Trace]:
        """Return traces that contain errors."""
        return [t for t in self.traces if t.has_errors]

    def get_failed_traces(self, threshold_score: float = 0.5) -> list[Trace]:
        """Return traces that are considered failures."""
        # This would integrate with evaluation results
        return [t for t in self.traces if t.error_count > 0]

    def tool_usage_patterns(self) -> dict[str, Any]:
        """Analyze patterns in tool usage across traces."""
        all_tools: dict[str, int] = defaultdict(int)
        tool_sequences: list[list[str]] = []

        for trace in self.traces:
            tools_in_trace = []
            for event in trace.events:
                if event.event_type == EventType.TOOL_CALL and event.tool_name:
                    all_tools[event.tool_name] += 1
                    tools_in_trace.append(event.tool_name)
            if tools_in_trace:
                tool_sequences.append(tools_in_trace)

        return {
            "total_tool_calls": sum(all_tools.values()),
            "unique_tools": list(all_tools.keys()),
            "tool_frequency": dict(all_tools),
            "avg_tools_per_trace": sum(len(s) for s in tool_sequences) / max(len(tool_sequences), 1),
            "common_sequences": self._find_common_sequences(tool_sequences),
        }

    def _find_common_sequences(self, sequences: list[list[str]], min_support: int = 2) -> list[tuple[list[str], int]]:
        """Find commonly repeated tool call sequences."""
        from collections import Counter

        # Look for pairs and triples
        subsequences: list[tuple[str, ...]] = []
        for seq in sequences:
            for i in range(len(seq) - 1):
                subsequences.append((seq[i], seq[i + 1]))
            for i in range(len(seq) - 2):
                subsequences.append((seq[i], seq[i + 1], seq[i + 2]))

        counts = Counter(subsequences)
        return [(list(seq), count) for seq, count in counts.most_common(10) if count >= min_support]

    def latency_analysis(self) -> dict[str, Any]:
        """Analyze latency patterns."""
        durations = [t.duration_sec for t in self.traces]
        llm_latencies: list[float] = []
        tool_latencies: list[float] = []

        for trace in self.traces:
            for event in trace.events:
                if event.event_type == EventType.LLM_REQUEST:
                    llm_latencies.append(event.latency_ms)
                elif event.event_type == EventType.TOOL_CALL:
                    tool_latencies.append(event.latency_ms)

        return {
            "trace_duration": {
                "mean": float(np.mean(durations)) if durations else 0,
                "median": float(np.median(durations)) if durations else 0,
                "p95": float(np.percentile(durations, 95)) if durations else 0,
                "max": max(durations) if durations else 0,
            },
            "llm_latency_ms": {
                "mean": float(np.mean(llm_latencies)) if llm_latencies else 0,
                "median": float(np.median(llm_latencies)) if llm_latencies else 0,
                "p95": float(np.percentile(llm_latencies, 95)) if llm_latencies else 0,
            },
            "tool_latency_ms": {
                "mean": float(np.mean(tool_latencies)) if tool_latencies else 0,
                "median": float(np.median(tool_latencies)) if tool_latencies else 0,
                "p95": float(np.percentile(tool_latencies, 95)) if tool_latencies else 0,
            },
        }

    def generate_report(self) -> dict[str, Any]:
        """Generate a comprehensive analysis report."""
        return {
            "total_traces": len(self.traces),
            "error_traces": len(self.get_error_traces()),
            "tool_usage": self.tool_usage_patterns(),
            "latency": self.latency_analysis(),
            "event_distribution": self._event_distribution(),
        }

    def _event_distribution(self) -> dict[str, int]:
        """Count all event types across traces."""
        counts: dict[str, int] = defaultdict(int)
        for trace in self.traces:
            for event_type, count in trace.event_counts.items():
                counts[event_type] += count
        return dict(counts)
