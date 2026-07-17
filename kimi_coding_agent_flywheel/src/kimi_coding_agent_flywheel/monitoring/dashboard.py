"""
Production Monitoring Dashboard for Coding Agent Quality Flywheel.

Tracks agent behavior in production:
1. Real-time success/failure rates
2. Drift detection across multiple dimensions
3. Failure mode trending
4. Alert generation

Inspired by Noveum, Langfuse, and production LLM observability patterns.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np


# -----------------------------------------------------------------------------
# Data Structures
# -----------------------------------------------------------------------------

@dataclass
class ProductionTrace:
    """A lightweight trace from production agent execution."""
    trace_id: str
    timestamp: datetime
    agent_name: str
    model_id: str | None = None
    task_type: str = ""

    # Outcome
    success: bool = False
    score: float = 0.0
    duration_sec: float = 0.0
    token_count: int = 0
    cost_usd: float = 0.0

    # Failure info (if success=False)
    failure_category: str | None = None
    failure_subcategory: str | None = None
    error_message: str = ""

    # Tool usage
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    # User feedback
    user_feedback: str | None = None  # "positive", "negative", "corrected"
    user_correction: str | None = None  # What the user had to fix

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "timestamp": self.timestamp.isoformat(),
            "agent_name": self.agent_name,
            "model_id": self.model_id,
            "task_type": self.task_type,
            "success": self.success,
            "score": self.score,
            "duration_sec": self.duration_sec,
            "token_count": self.token_count,
            "cost_usd": self.cost_usd,
            "failure_category": self.failure_category,
            "failure_subcategory": self.failure_subcategory,
            "error_message": self.error_message,
            "tool_calls": self.tool_calls,
            "user_feedback": self.user_feedback,
            "user_correction": self.user_correction,
        }


@dataclass
class TimeSeriesMetric:
    """A time-series metric with statistical properties."""
    name: str
    unit: str = ""
    values: deque[tuple[datetime, float]] = field(default_factory=lambda: deque(maxlen=10000))

    def add(self, value: float, timestamp: datetime | None = None) -> None:
        ts = timestamp or datetime.utcnow()
        self.values.append((ts, value))

    def get_window(self, window_minutes: int = 60) -> list[float]:
        """Get values within the specified time window."""
        cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)
        return [v for ts, v in self.values if ts >= cutoff]

    def get_stats(self, window_minutes: int = 60) -> dict[str, float]:
        """Get statistics for the specified time window."""
        values = self.get_window(window_minutes)
        if not values:
            return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "count": 0}

        arr = np.array(values)
        return {
            "mean": float(np.mean(arr)),
            "std": float(np.std(arr)),
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
            "count": len(values),
            "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)),
            "p99": float(np.percentile(arr, 99)),
        }


@dataclass
class DriftAlert:
    """An alert generated when drift is detected."""
    alert_id: str
    timestamp: datetime
    alert_type: str  # "performance", "behavioral", "cost", "failure_mode"
    severity: str    # "info", "warning", "critical"

    metric_name: str
    baseline_value: float
    current_value: float
    deviation: float

    description: str
    affected_tasks: list[str] = field(default_factory=list)
    recommended_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "timestamp": self.timestamp.isoformat(),
            "alert_type": self.alert_type,
            "severity": self.severity,
            "metric_name": self.metric_name,
            "baseline_value": self.baseline_value,
            "current_value": self.current_value,
            "deviation": self.deviation,
            "description": self.description,
            "affected_tasks": self.affected_tasks,
            "recommended_action": self.recommended_action,
        }


# -----------------------------------------------------------------------------
# Production Monitor
# -----------------------------------------------------------------------------

class ProductionMonitor:
    """
    Monitors coding agent performance in production.

    Key capabilities:
    - Ingest production traces
    - Track metrics over time
    - Detect drift from baseline
    - Generate alerts
    - Export data for analysis
    """

    def __init__(self, window_hours: int = 24):
        self.window_hours = window_hours

        # Metrics
        self.metrics: dict[str, TimeSeriesMetric] = {
            "success_rate": TimeSeriesMetric("success_rate", "ratio"),
            "avg_score": TimeSeriesMetric("avg_score", "ratio"),
            "avg_duration": TimeSeriesMetric("avg_duration", "seconds"),
            "avg_tokens": TimeSeriesMetric("avg_tokens", "count"),
            "avg_cost": TimeSeriesMetric("avg_cost", "usd"),
            "error_rate": TimeSeriesMetric("error_rate", "ratio"),
            "tool_call_count": TimeSeriesMetric("tool_call_count", "count"),
        }

        # Failure tracking
        self.failure_counts: dict[str, deque[tuple[datetime, int]]] = defaultdict(
            lambda: deque(maxlen=10000)
        )

        # Traces (recent only)
        self.recent_traces: deque[ProductionTrace] = deque(maxlen=1000)

        # Baselines
        self.baselines: dict[str, float] = {}
        self.baseline_established: bool = False

        # Alerts
        self.alerts: list[DriftAlert] = []
        self.alert_handlers: list[Callable[[DriftAlert], None]] = []

        # Alert thresholds
        self.thresholds = {
            "success_rate_drop": 0.10,      # 10% drop triggers warning
            "success_rate_critical": 0.20,  # 20% drop triggers critical
            "error_rate_spike": 0.05,       # 5% error rate triggers warning
            "error_rate_critical": 0.10,    # 10% error rate triggers critical
            "cost_increase": 0.50,          # 50% cost increase triggers warning
            "duration_increase": 0.50,      # 50% slower triggers warning
        }

    def ingest_trace(self, trace: ProductionTrace) -> None:
        """Ingest a production execution trace."""
        self.recent_traces.append(trace)

        # Update failure counts
        if not trace.success and trace.failure_subcategory:
            self.failure_counts[trace.failure_subcategory].append(
                (trace.timestamp, 1)
            )

    def ingest_batch(self, traces: list[ProductionTrace]) -> None:
        """Ingest multiple traces at once."""
        for trace in traces:
            self.ingest_trace(trace)

    def compute_metrics(self, window_minutes: int = 60) -> dict[str, dict[str, float]]:
        """Compute all metrics for the specified time window."""
        cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)
        recent = [t for t in self.recent_traces if t.timestamp >= cutoff]

        if not recent:
            return {name: {"mean": 0.0, "count": 0} for name in self.metrics}

        # Success rate
        success_count = sum(1 for t in recent if t.success)
        success_rate = success_count / len(recent)
        self.metrics["success_rate"].add(success_rate)

        # Average score
        scores = [t.score for t in recent]
        avg_score = np.mean(scores) if scores else 0.0
        self.metrics["avg_score"].add(avg_score)

        # Average duration
        durations = [t.duration_sec for t in recent]
        avg_duration = np.mean(durations) if durations else 0.0
        self.metrics["avg_duration"].add(avg_duration)

        # Average tokens
        tokens = [t.token_count for t in recent]
        avg_tokens = np.mean(tokens) if tokens else 0.0
        self.metrics["avg_tokens"].add(avg_tokens)

        # Average cost
        costs = [t.cost_usd for t in recent]
        avg_cost = np.mean(costs) if costs else 0.0
        self.metrics["avg_cost"].add(avg_cost)

        # Error rate
        error_rate = 1.0 - success_rate
        self.metrics["error_rate"].add(error_rate)

        # Tool calls
        tool_counts = [len(t.tool_calls) for t in recent]
        avg_tools = np.mean(tool_counts) if tool_counts else 0.0
        self.metrics["tool_call_count"].add(avg_tools)

        return {
            name: metric.get_stats(window_minutes)
            for name, metric in self.metrics.items()
        }

    def establish_baseline(self, duration_minutes: int = 60) -> dict[str, float]:
        """Establish baseline metrics from recent production data."""
        metrics = self.compute_metrics(duration_minutes)

        self.baselines = {
            "success_rate": metrics["success_rate"]["mean"],
            "avg_score": metrics["avg_score"]["mean"],
            "avg_duration": metrics["avg_duration"]["mean"],
            "avg_tokens": metrics["avg_tokens"]["mean"],
            "avg_cost": metrics["avg_cost"]["mean"],
            "error_rate": metrics["error_rate"]["mean"],
        }
        self.baseline_established = True

        return self.baselines

    def check_drift(self) -> list[DriftAlert]:
        """Check for drift from baseline and generate alerts."""
        if not self.baseline_established:
            return []

        current = self.compute_metrics(60)
        new_alerts = []

        # Success rate drift
        baseline_sr = self.baselines.get("success_rate", 1.0)
        current_sr = current["success_rate"]["mean"]
        sr_drop = baseline_sr - current_sr

        if sr_drop > self.thresholds["success_rate_critical"]:
            new_alerts.append(DriftAlert(
                alert_id=f"sr_critical_{datetime.utcnow().strftime('%H%M%S')}",
                timestamp=datetime.utcnow(),
                alert_type="performance",
                severity="critical",
                metric_name="success_rate",
                baseline_value=baseline_sr,
                current_value=current_sr,
                deviation=sr_drop,
                description=f"Success rate dropped {sr_drop:.1%} from baseline",
                recommended_action="Immediately investigate recent changes (prompt, model, tools)",
            ))
        elif sr_drop > self.thresholds["success_rate_drop"]:
            new_alerts.append(DriftAlert(
                alert_id=f"sr_warn_{datetime.utcnow().strftime('%H%M%S')}",
                timestamp=datetime.utcnow(),
                alert_type="performance",
                severity="warning",
                metric_name="success_rate",
                baseline_value=baseline_sr,
                current_value=current_sr,
                deviation=sr_drop,
                description=f"Success rate dropped {sr_drop:.1%} from baseline",
                recommended_action="Monitor closely and prepare rollback plan",
            ))

        # Error rate spike
        baseline_er = self.baselines.get("error_rate", 0.0)
        current_er = current["error_rate"]["mean"]
        er_increase = current_er - baseline_er

        if current_er > self.thresholds["error_rate_critical"]:
            # Get most common recent failures
            recent_failures = self._get_top_failures(60, 3)
            new_alerts.append(DriftAlert(
                alert_id=f"er_critical_{datetime.utcnow().strftime('%H%M%S')}",
                timestamp=datetime.utcnow(),
                alert_type="behavioral",
                severity="critical",
                metric_name="error_rate",
                baseline_value=baseline_er,
                current_value=current_er,
                deviation=er_increase,
                description=f"Error rate at {current_er:.1%} (baseline: {baseline_er:.1%})",
                affected_tasks=[f[0] for f in recent_failures],
                recommended_action=f"Top failures: {', '.join(f[0] for f in recent_failures)}",
            ))

        # Cost drift
        baseline_cost = self.baselines.get("avg_cost", 0.0)
        current_cost = current["avg_cost"]["mean"]
        if baseline_cost > 0:
            cost_increase = (current_cost - baseline_cost) / baseline_cost
            if cost_increase > self.thresholds["cost_increase"]:
                new_alerts.append(DriftAlert(
                    alert_id=f"cost_warn_{datetime.utcnow().strftime('%H%M%S')}",
                    timestamp=datetime.utcnow(),
                    alert_type="cost",
                    severity="warning",
                    metric_name="avg_cost",
                    baseline_value=baseline_cost,
                    current_value=current_cost,
                    deviation=cost_increase,
                    description=f"Cost increased {cost_increase:.1%} from baseline",
                    recommended_action="Review token usage and optimize prompts",
                ))

        # Store and notify
        for alert in new_alerts:
            self.alerts.append(alert)
            for handler in self.alert_handlers:
                handler(alert)

        return new_alerts

    def _get_top_failures(self, window_minutes: int, n: int) -> list[tuple[str, int]]:
        """Get the most common failures in the recent window."""
        cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)
        counts: dict[str, int] = {}

        for subcategory, events in self.failure_counts.items():
            count = sum(1 for ts, _ in events if ts >= cutoff)
            if count > 0:
                counts[subcategory] = count

        return sorted(counts.items(), key=lambda x: x[1], reverse=True)[:n]

    def on_alert(self, handler: Callable[[DriftAlert], None]) -> None:
        """Register an alert handler."""
        self.alert_handlers.append(handler)

    def get_failure_trends(self, window_hours: int = 24) -> dict[str, list[tuple[str, int]]]:
        """Get failure trends over time buckets."""
        now = datetime.utcnow()
        buckets: dict[str, dict[str, int]] = {}

        for subcategory, events in self.failure_counts.items():
            for ts, _ in events:
                if now - ts <= timedelta(hours=window_hours):
                    hour_key = ts.strftime("%Y-%m-%d %H:00")
                    if hour_key not in buckets:
                        buckets[hour_key] = {}
                    buckets[hour_key][subcategory] = buckets[hour_key].get(subcategory, 0) + 1

        # Convert to sorted list
        result = {}
        for hour_key in sorted(buckets.keys()):
            result[hour_key] = sorted(
                buckets[hour_key].items(),
                key=lambda x: x[1],
                reverse=True,
            )

        return result

    def generate_dashboard_data(self) -> dict[str, Any]:
        """Generate data for the monitoring dashboard."""
        metrics = self.compute_metrics(60)

        # Recent alerts
        recent_alerts = [
            a.to_dict() for a in self.alerts
            if a.timestamp > datetime.utcnow() - timedelta(hours=24)
        ]

        # Failure distribution
        failure_dist = self._get_top_failures(60, 10)

        # Traces summary
        recent_traces = [t.to_dict() for t in list(self.recent_traces)[-100:]]

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "metrics": metrics,
            "baselines": self.baselines if self.baseline_established else None,
            "recent_alerts": recent_alerts,
            "failure_distribution": failure_dist,
            "total_traces_ingested": len(self.recent_traces),
            "recent_traces": recent_traces,
        }

    def export_for_analysis(self, filepath: str) -> None:
        """Export all data for offline analysis."""
        data = {
            "metrics": {
                name: [(ts.isoformat(), v) for ts, v in metric.values]
                for name, metric in self.metrics.items()
            },
            "failure_counts": {
                cat: [(ts.isoformat(), v) for ts, v in events]
                for cat, events in self.failure_counts.items()
            },
            "traces": [t.to_dict() for t in self.recent_traces],
            "alerts": [a.to_dict() for a in self.alerts],
            "baselines": self.baselines,
        }

        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)


# -----------------------------------------------------------------------------
# Simple Console Dashboard Renderer
# -----------------------------------------------------------------------------

class ConsoleDashboard:
    """Renders monitoring data to the console."""

    @staticmethod
    def render(monitor: ProductionMonitor) -> None:
        """Render the dashboard to console."""
        data = monitor.generate_dashboard_data()

        print("\n" + "=" * 70)
        print(f"  CODING AGENT PRODUCTION MONITOR  |  {data['timestamp']}")
        print("=" * 70)

        # Metrics
        print("\n  CURRENT METRICS (last hour):")
        print("  " + "-" * 50)
        for name, stats in data["metrics"].items():
            if stats["count"] > 0:
                print(f"  {name:20s}: {stats['mean']:.3f} (p95: {stats.get('p95', 0):.3f})")

        # Baseline comparison
        if data["baselines"]:
            print("\n  BASELINE COMPARISON:")
            print("  " + "-" * 50)
            current = data["metrics"]
            for metric_name, baseline_val in data["baselines"].items():
                current_val = current.get(metric_name, {}).get("mean", 0)
                delta = current_val - baseline_val
                delta_pct = (delta / baseline_val * 100) if baseline_val != 0 else 0
                symbol = "+" if delta >= 0 else ""
                print(f"  {metric_name:20s}: {current_val:.3f} vs {baseline_val:.3f} "
                      f"({symbol}{delta:.3f}, {symbol}{delta_pct:.1f}%)")

        # Alerts
        if data["recent_alerts"]:
            print("\n  RECENT ALERTS:")
            print("  " + "-" * 50)
            for alert in data["recent_alerts"][-5:]:
                severity_icon = "!!" if alert["severity"] == "critical" else "!"
                print(f"  [{severity_icon}] {alert['alert_type']:15s}: {alert['description'][:60]}")

        # Failure distribution
        if data["failure_distribution"]:
            print("\n  TOP FAILURE MODES (last hour):")
            print("  " + "-" * 50)
            for failure, count in data["failure_distribution"]:
                print(f"  {failure:40s}: {count:4d}")

        print("\n" + "=" * 70)


# -----------------------------------------------------------------------------
# User Feedback Collector
# -----------------------------------------------------------------------------

class UserFeedbackCollector:
    """
    Collects and processes user feedback on agent outputs.

    User feedback is the highest-quality signal for the quality flywheel
    because it represents ground-truth about whether the agent succeeded.
    """

    def __init__(self, storage_path: str = "data/feedback"):
        self.storage_path = Path(storage_path)
        self.feedback_entries: list[dict[str, Any]] = []

    def record_feedback(
        self,
        trace_id: str,
        was_correct: bool,
        user_notes: str = "",
        user_correction: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """Record user feedback for a trace."""
        entry = {
            "trace_id": trace_id,
            "task_id": task_id,
            "was_correct": was_correct,
            "user_notes": user_notes,
            "user_correction": user_correction,
            "timestamp": datetime.utcnow().isoformat(),
        }

        self.feedback_entries.append(entry)
        self._save_entry(entry)

    def get_incorrect_outputs(self, since_hours: int = 24) -> list[dict[str, Any]]:
        """Get all incorrect outputs for analysis."""
        cutoff = datetime.utcnow() - timedelta(hours=since_hours)
        return [
            e for e in self.feedback_entries
            if not e["was_correct"] and datetime.fromisoformat(e["timestamp"]) >= cutoff
        ]

    def get_feedback_stats(self) -> dict[str, Any]:
        """Get statistics on user feedback."""
        if not self.feedback_entries:
            return {"total": 0, "correct_rate": 0.0}

        total = len(self.feedback_entries)
        correct = sum(1 for e in self.feedback_entries if e["was_correct"])

        return {
            "total": total,
            "correct": correct,
            "incorrect": total - correct,
            "correct_rate": correct / total,
            "with_corrections": sum(1 for e in self.feedback_entries if e.get("user_correction")),
        }

    def _save_entry(self, entry: dict[str, Any]) -> None:
        """Persist feedback entry."""
        self.storage_path.mkdir(parents=True, exist_ok=True)
        filepath = self.storage_path / f"{entry['trace_id']}.json"
        with open(filepath, "w") as f:
            json.dump(entry, f, indent=2)
