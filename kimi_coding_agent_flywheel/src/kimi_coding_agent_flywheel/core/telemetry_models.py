"""Serializable telemetry events, spans, and traces."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any



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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TraceEvent:
        """Reconstruct an event from the trace JSON schema."""
        return cls(
            event_id=str(data["event_id"]),
            event_type=EventType[str(data["event_type"])],
            timestamp=datetime.fromisoformat(str(data["timestamp"])),
            step_number=int(data.get("step_number", 0)),
            content=str(data.get("content", "")),
            metadata=dict(data.get("metadata", {})),
            model=data.get("model"),
            messages=data.get("messages"),
            response=data.get("response"),
            tokens_in=int(data.get("tokens_in", 0)),
            tokens_out=int(data.get("tokens_out", 0)),
            latency_ms=float(data.get("latency_ms", 0.0)),
            tool_name=data.get("tool_name"),
            tool_input=data.get("tool_input"),
            tool_output=data.get("tool_output"),
            tool_error=data.get("tool_error"),
            decision_options=data.get("decision_options"),
            decision_choice=data.get("decision_choice"),
            decision_reasoning=data.get("decision_reasoning"),
            parent_event_id=data.get("parent_event_id"),
            span_id=data.get("span_id"),
        )


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

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        event_lookup: dict[str, TraceEvent] | None = None,
    ) -> TraceSpan:
        """Reconstruct a span and reuse top-level event objects when possible."""
        events: list[TraceEvent] = []
        for event_data in data.get("events", []):
            event_id = str(event_data.get("event_id", ""))
            event = event_lookup.get(event_id) if event_lookup is not None else None
            if event is None:
                event = TraceEvent.from_dict(event_data)
                if event_lookup is not None:
                    event_lookup[event.event_id] = event
            events.append(event)
        end_time = data.get("end_time")
        return cls(
            span_id=str(data["span_id"]),
            name=str(data["name"]),
            start_time=datetime.fromisoformat(str(data["start_time"])),
            end_time=datetime.fromisoformat(end_time) if isinstance(end_time, str) else None,
            parent_span_id=data.get("parent_span_id"),
            events=events,
            metadata=dict(data.get("metadata", {})),
        )


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
    def from_dict(cls, data: dict[str, Any]) -> Trace:
        """Reconstruct a trace while preserving spans and recording counters."""
        events = [TraceEvent.from_dict(event_data) for event_data in data.get("events", [])]
        event_lookup = {event.event_id: event for event in events}
        spans = {
            str(span_id): TraceSpan.from_dict(span_data, event_lookup)
            for span_id, span_data in data.get("spans", {}).items()
        }
        end_time = data.get("end_time")
        trace = cls(
            trace_id=str(data["trace_id"]),
            agent_name=str(data["agent_name"]),
            model_id=data.get("model_id"),
            task_id=data.get("task_id"),
            start_time=datetime.fromisoformat(str(data["start_time"])),
            end_time=datetime.fromisoformat(end_time) if isinstance(end_time, str) else None,
            events=events,
            spans=spans,
            system_prompt=data.get("system_prompt"),
            model_params=dict(data.get("model_params", {})),
            tool_definitions=list(data.get("tool_definitions", [])),
            total_tokens=int(
                data.get(
                    "total_tokens",
                    sum(
                        event.tokens_in + event.tokens_out
                        for event in events
                        if event.event_type == EventType.LLM_REQUEST
                    ),
                )
            ),
            total_cost_usd=float(data.get("total_cost_usd", 0.0)),
            total_tool_calls=int(
                data.get(
                    "total_tool_calls",
                    sum(event.event_type == EventType.TOOL_CALL for event in events),
                )
            ),
            total_llm_calls=int(
                data.get(
                    "total_llm_calls",
                    sum(event.event_type == EventType.LLM_REQUEST for event in events),
                )
            ),
        )
        trace._open_spans = {
            span_id: span
            for span_id, span in spans.items()
            if span.end_time is None
        }
        trace._step_counter = max(
            (event.step_number for event in events),
            default=-1,
        ) + 1
        return trace

    @classmethod
    def load(cls, filepath: str) -> Trace:
        """Load a trace from disk."""
        with open(filepath) as f:
            data = json.load(f)
        return cls.from_dict(data)
