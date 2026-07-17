"""Read-only analysis of in-memory telemetry traces."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from .telemetry_models import EventType, Trace

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
