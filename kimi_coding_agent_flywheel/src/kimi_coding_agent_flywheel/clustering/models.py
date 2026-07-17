"""Data transfer objects shared by failure-analysis stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

def utc_now() -> datetime:
    return datetime.now(UTC)


# -----------------------------------------------------------------------------
# Data Structures
# -----------------------------------------------------------------------------

@dataclass
class FailureInstance:
    """A single identified failure from an agent execution."""
    failure_id: str
    task_id: str
    agent_name: str
    model_id: str | None = None

    # Failure classification
    category: str | None = None          # Top-level category
    subcategory: str | None = None       # Specific failure type
    description: str = ""                 # Human-readable description
    severity: str = "medium"              # "low", "medium", "high", "critical"

    # Source data
    trace_id: str | None = None
    trace_snippet: str = ""               # Relevant excerpt from trace
    error_message: str = ""
    failing_test: str | None = None

    # Analysis
    probable_cause: str = ""
    suggested_fix: str = ""
    affected_prompt_component: str | None = None  # Which part of prompt caused this

    # Metadata
    timestamp: datetime = field(default_factory=utc_now)
    llm_judge_score: float | None = None  # 0-10 from LLM judge
    embedding: list[float] | None = None   # Vector representation for clustering
    cluster_confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_id": self.failure_id,
            "task_id": self.task_id,
            "agent_name": self.agent_name,
            "model_id": self.model_id,
            "category": self.category,
            "subcategory": self.subcategory,
            "description": self.description,
            "severity": self.severity,
            "trace_id": self.trace_id,
            "trace_snippet": self.trace_snippet,
            "error_message": self.error_message,
            "failing_test": self.failing_test,
            "probable_cause": self.probable_cause,
            "suggested_fix": self.suggested_fix,
            "affected_prompt_component": self.affected_prompt_component,
            "timestamp": self.timestamp.isoformat(),
            "llm_judge_score": self.llm_judge_score,
            "embedding": self.embedding,
            "cluster_confidence": self.cluster_confidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FailureInstance:
        """Reconstruct a failure from persisted analysis state."""
        timestamp = data.get("timestamp")
        parsed_timestamp = utc_now()
        if isinstance(timestamp, str):
            try:
                parsed_timestamp = datetime.fromisoformat(timestamp)
                if parsed_timestamp.tzinfo is None:
                    parsed_timestamp = parsed_timestamp.replace(tzinfo=UTC)
                else:
                    parsed_timestamp = parsed_timestamp.astimezone(UTC)
            except ValueError:
                pass

        return cls(
            failure_id=str(data.get("failure_id", "")),
            task_id=str(data.get("task_id", "unknown")),
            agent_name=str(data.get("agent_name", "unknown")),
            model_id=data.get("model_id"),
            category=data.get("category"),
            subcategory=data.get("subcategory"),
            description=str(data.get("description", "")),
            severity=str(data.get("severity", "medium")),
            trace_id=data.get("trace_id"),
            trace_snippet=str(data.get("trace_snippet", "")),
            error_message=str(data.get("error_message", "")),
            failing_test=data.get("failing_test"),
            probable_cause=str(data.get("probable_cause", "")),
            suggested_fix=str(data.get("suggested_fix", "")),
            affected_prompt_component=data.get("affected_prompt_component"),
            timestamp=parsed_timestamp,
            llm_judge_score=data.get("llm_judge_score"),
            embedding=data.get("embedding"),
            cluster_confidence=data.get("cluster_confidence"),
        )


@dataclass
class EmbeddedFailure:
    """A failure and the exact text used to embed it."""
    failure: FailureInstance
    embedding_text: str
    causal_text: str = ""
    surface_text: str = ""


@dataclass
class FailureCluster:
    """A cluster of similar failures identified through embedding analysis."""
    cluster_id: int
    label: str                            # Auto-generated descriptive label
    description: str = ""

    # Cluster contents
    failures: list[FailureInstance] = field(default_factory=list)

    # Statistics
    dominant_category: str | None = None
    dominant_subcategory: str | None = None
    affected_agents: set[str] = field(default_factory=set)
    affected_models: set[str] = field(default_factory=set)

    # Pattern analysis
    common_keywords: list[str] = field(default_factory=list)
    common_tool_calls: list[str] = field(default_factory=list)
    avg_severity: str = "medium"

    # Actionable insights
    suggested_prompt_fix: str = ""
    suggested_tool_fix: str = ""
    regression_tests_needed: list[str] = field(default_factory=list)
    assignment_type: str = "dbscan"

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "label": self.label,
            "description": self.description,
            "failure_count": len(self.failures),
            "dominant_category": self.dominant_category,
            "dominant_subcategory": self.dominant_subcategory,
            "affected_agents": list(self.affected_agents),
            "affected_models": list(self.affected_models),
            "common_keywords": self.common_keywords,
            "common_tool_calls": self.common_tool_calls,
            "avg_severity": self.avg_severity,
            "suggested_prompt_fix": self.suggested_prompt_fix,
            "suggested_tool_fix": self.suggested_tool_fix,
            "regression_tests_needed": self.regression_tests_needed,
            "assignment_type": self.assignment_type,
        }


@dataclass
class DiagnosedFailures:
    """Successful diagnosis result for one trace."""
    failures: list[FailureInstance]

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "diagnosed_failures",
            "failures": [failure.to_dict() for failure in self.failures],
        }


@dataclass
class DiagnosisInfrastructureError:
    """The judge failed outside the analyzed agent run."""
    trace_id: str | None
    task_id: str
    agent_name: str
    message: str
    exception_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "infrastructure_error",
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "agent_name": self.agent_name,
            "message": self.message,
            "exception_type": self.exception_type,
        }


@dataclass
class DiagnosisInvalidResponse:
    """The judge responded, but not with the required schema."""
    trace_id: str | None
    task_id: str
    agent_name: str
    message: str
    response_excerpt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "invalid_response",
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "agent_name": self.agent_name,
            "message": self.message,
            "response_excerpt": self.response_excerpt,
        }


DiagnosisResult = DiagnosedFailures | DiagnosisInfrastructureError | DiagnosisInvalidResponse
DiagnosisError = DiagnosisInfrastructureError | DiagnosisInvalidResponse
