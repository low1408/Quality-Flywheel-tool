"""
Failure Clustering and Root Cause Analysis for Coding Agent Quality Flywheel.

This module implements:
1. LLM-as-judge failure diagnosis (inspired by Composo.ai)
2. Embedding-based failure clustering
3. Taxonomy-based classification (inspired by MAST - Multi-Agent System Taxonomy)
4. Root cause analysis and pattern extraction
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
from sklearn.cluster import DBSCAN, HDBSCAN
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


# -----------------------------------------------------------------------------
# Failure Taxonomy (inspired by MAST - Multi-Agent System Failure Taxonomy)
# -----------------------------------------------------------------------------

class FailureCategory:
    """Hierarchical failure classification system."""

    # Specification & System Design Failures (~42% of failures)
    SPECIFICATION = "specification"
    SUBCATEGORY_DISOBEY_SPEC = "disobey_task_specification"
    SUBCATEGORY_MISSING_CONSTRAINT = "missing_role_constraint"
    SUBCATEGORY_REPETITION = "repeating_previous_steps"
    SUBCATEGORY_NO_TERMINATION = "failure_to_terminate"
    SUBCATEGORY_AMBIGUOUS_PROMPT = "ambiguous_prompt_interpretation"

    # Inter-Agent / Tool Misalignment (~37% of failures)
    TOOL_MISALIGNMENT = "tool_misalignment"
    SUBCATEGORY_WRONG_TOOL = "wrong_tool_selected"
    SUBCATEGORY_WRONG_ARGS = "incorrect_tool_arguments"
    SUBCATEGORY_TOOL_NOT_FOUND = "tool_not_found_or_unavailable"
    SUBCATEGORY_IGNORE_OUTPUT = "ignoring_tool_output"
    SUBCATEGORY_REPEATED_TOOL_ERRORS = "repeated_tool_errors"

    # Task Verification & Quality Control (~21% of failures)
    VERIFICATION = "verification"
    SUBCATEGORY_PREMATURE_STOP = "premature_task_termination"
    SUBCATEGORY_NO_VALIDATION = "skipping_validation"
    SUBCATEGORY_ACCEPTING_INCORRECT = "accepting_incorrect_solution"
    SUBCATEGORY_PARTIAL_SOLUTION = "partial_solution_accepted"

    # Code-Specific Failures
    CODE_SYNTAX = "code_syntax_error"
    CODE_LOGIC = "code_logic_error"
    CODE_RUNTIME = "code_runtime_error"
    CODE_IMPORT = "import_or_dependency_error"
    CODE_TYPE = "type_error"

    # Environment Failures
    ENV_SETUP = "environment_setup_failure"
    ENV_MISSING_DEP = "missing_dependency"
    ENV_PERMISSION = "permission_error"
    ENV_TIMEOUT = "execution_timeout"

    # LLM API Failures
    LLM_RATE_LIMIT = "rate_limit_exceeded"
    LLM_CONTEXT_WINDOW = "context_window_exceeded"
    LLM_REFUSAL = "model_refusal"
    LLM_HALLUCINATION = "model_hallucination"

    # Prompt Engineering Failures
    PROMPT_TOO_VAGUE = "prompt_too_vague"
    PROMPT_TOO_LONG = "prompt_context_overflow"
    PROMPT_FORMAT = "output_format_misunderstanding"


# Human-readable descriptions for each subcategory
FAILURE_DESCRIPTIONS: dict[str, str] = {
    FailureCategory.SUBCATEGORY_DISOBEY_SPEC: "Agent did not follow the task instructions",
    FailureCategory.SUBCATEGORY_MISSING_CONSTRAINT: "Agent violated implicit constraints",
    FailureCategory.SUBCATEGORY_REPETITION: "Agent repeated previously completed work",
    FailureCategory.SUBCATEGORY_NO_TERMINATION: "Agent failed to recognize task completion",
    FailureCategory.SUBCATEGORY_AMBIGUOUS_PROMPT: "Agent misinterpreted ambiguous instructions",
    FailureCategory.SUBCATEGORY_WRONG_TOOL: "Agent selected the wrong tool for the job",
    FailureCategory.SUBCATEGORY_WRONG_ARGS: "Agent provided incorrect arguments to a tool",
    FailureCategory.SUBCATEGORY_TOOL_NOT_FOUND: "Agent tried to use a non-existent tool",
    FailureCategory.SUBCATEGORY_IGNORE_OUTPUT: "Agent ignored or misinterpreted tool output",
    FailureCategory.SUBCATEGORY_REPEATED_TOOL_ERRORS: "Agent repeatedly failed with the same tool",
    FailureCategory.SUBCATEGORY_PREMATURE_STOP: "Agent stopped before completing the task",
    FailureCategory.SUBCATEGORY_NO_VALIDATION: "Agent did not verify its solution",
    FailureCategory.SUBCATEGORY_ACCEPTING_INCORRECT: "Agent accepted a wrong solution",
    FailureCategory.SUBCATEGORY_PARTIAL_SOLUTION: "Agent delivered incomplete work",
    FailureCategory.CODE_SYNTAX: "Generated code has syntax errors",
    FailureCategory.CODE_LOGIC: "Generated code has logical errors",
    FailureCategory.CODE_RUNTIME: "Generated code fails at runtime",
    FailureCategory.CODE_IMPORT: "Generated code has import/dependency errors",
    FailureCategory.CODE_TYPE: "Generated code has type errors",
    FailureCategory.ENV_SETUP: "Failed to set up execution environment",
    FailureCategory.ENV_MISSING_DEP: "Missing required dependencies",
    FailureCategory.ENV_PERMISSION: "Permission denied during execution",
    FailureCategory.ENV_TIMEOUT: "Execution timed out",
    FailureCategory.LLM_RATE_LIMIT: "Hit rate limit during LLM calls",
    FailureCategory.LLM_CONTEXT_WINDOW: "Exceeded LLM context window",
    FailureCategory.LLM_REFUSAL: "Model refused to perform the task",
    FailureCategory.LLM_HALLUCINATION: "Model hallucinated non-existent APIs or behavior",
    FailureCategory.PROMPT_TOO_VAGUE: "System prompt was too vague for the task",
    FailureCategory.PROMPT_TOO_LONG: "Context overflow due to excessive prompt length",
    FailureCategory.PROMPT_FORMAT: "Agent misunderstood required output format",
}


# Category groupings for high-level analysis
CATEGORY_GROUPS: dict[str, list[str]] = {
    "Specification Issues": [
        FailureCategory.SUBCATEGORY_DISOBEY_SPEC,
        FailureCategory.SUBCATEGORY_MISSING_CONSTRAINT,
        FailureCategory.SUBCATEGORY_REPETITION,
        FailureCategory.SUBCATEGORY_NO_TERMINATION,
        FailureCategory.SUBCATEGORY_AMBIGUOUS_PROMPT,
    ],
    "Tool Misalignment": [
        FailureCategory.SUBCATEGORY_WRONG_TOOL,
        FailureCategory.SUBCATEGORY_WRONG_ARGS,
        FailureCategory.SUBCATEGORY_TOOL_NOT_FOUND,
        FailureCategory.SUBCATEGORY_IGNORE_OUTPUT,
        FailureCategory.SUBCATEGORY_REPEATED_TOOL_ERRORS,
    ],
    "Verification Failures": [
        FailureCategory.SUBCATEGORY_PREMATURE_STOP,
        FailureCategory.SUBCATEGORY_NO_VALIDATION,
        FailureCategory.SUBCATEGORY_ACCEPTING_INCORRECT,
        FailureCategory.SUBCATEGORY_PARTIAL_SOLUTION,
    ],
    "Code Quality": [
        FailureCategory.CODE_SYNTAX,
        FailureCategory.CODE_LOGIC,
        FailureCategory.CODE_RUNTIME,
        FailureCategory.CODE_IMPORT,
        FailureCategory.CODE_TYPE,
    ],
    "Environment Issues": [
        FailureCategory.ENV_SETUP,
        FailureCategory.ENV_MISSING_DEP,
        FailureCategory.ENV_PERMISSION,
        FailureCategory.ENV_TIMEOUT,
    ],
    "LLM API Issues": [
        FailureCategory.LLM_RATE_LIMIT,
        FailureCategory.LLM_CONTEXT_WINDOW,
        FailureCategory.LLM_REFUSAL,
        FailureCategory.LLM_HALLUCINATION,
    ],
    "Prompt Engineering": [
        FailureCategory.PROMPT_TOO_VAGUE,
        FailureCategory.PROMPT_TOO_LONG,
        FailureCategory.PROMPT_FORMAT,
    ],
}

VALID_SEVERITIES = {"low", "medium", "high", "critical"}
ANALYSIS_STATE_SCHEMA_VERSION = 1


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
        )


@dataclass
class EmbeddedFailure:
    """A failure and the exact text used to embed it."""
    failure: FailureInstance
    embedding_text: str


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


# -----------------------------------------------------------------------------
# LLM-as-Judge Failure Diagnoser
# -----------------------------------------------------------------------------

class LLMJudgeDiagnoser:
    """
    Uses an LLM to diagnose failures from execution traces.

    Inspired by Composo.ai's criteria-less judging approach:
    - No static rubric that misses novel failure modes
    - Let the judge infer what a competent agent would do
    - Extract freeform diagnostic text for clustering
    """

    DIAGNOSIS_PROMPT_TEMPLATE = """You are an expert AI systems debugger.

Analyze the following agent execution trace and identify ALL failures.

AGENT: {agent_name}
MODEL: {model_id}
TASK: {task_description}

SYSTEM PROMPT:
```
{system_prompt}
```

EXECUTION TRACE:
```
{trace_snippet}
```

ERROR OUTPUT:
```
{error_output}
```

Your analysis should:
1. Identify what a competent agent would have done for this task
2. Compare the actual agent's behavior against that ideal
3. For each failure, provide:
   - The specific failure type (choose from: {failure_types})
   - Severity (low/medium/high/critical)
   - A 2-3 sentence diagnostic description
   - The likely root cause (prompt issue, tool issue, model limitation, etc.)
   - A specific suggestion for fixing it

4. Score the overall execution from 0-10

Format your response as JSON:
{{
  "overall_score": float,
  "failures": [
    {{
      "subcategory": str,
      "severity": str,
      "description": str,
      "root_cause": str,
      "suggested_fix": str,
      "affected_prompt_component": str | null
    }}
  ],
  "summary": str
}}
"""

    def __init__(
        self,
        judge_fn: Callable[[str], str] | None = None,
        *,
        use_mock_judge: bool = False,
    ):
        """
        Args:
            judge_fn: Function that takes a prompt string and returns the judge's response.
            use_mock_judge: Explicitly opt into the built-in testing mock.
        """
        if judge_fn is None and not use_mock_judge:
            raise ValueError(
                "LLMJudgeDiagnoser requires an explicit judge_fn. "
                "Pass use_mock_judge=True only in tests or demos."
            )
        self.judge_fn = judge_fn or self._mock_judge
        self._failure_type_list = ", ".join(FAILURE_DESCRIPTIONS.keys())

    def diagnose(self, trace_data: dict[str, Any], task_description: str = "") -> DiagnosisResult:
        """
        Diagnose failures from a single execution trace.

        Returns a discriminated result. Judge infrastructure failures and invalid
        judge responses are not converted into agent failures.
        """
        # Build the diagnosis prompt
        prompt = self.DIAGNOSIS_PROMPT_TEMPLATE.format(
            agent_name=trace_data.get("agent_name", "unknown"),
            model_id=trace_data.get("model_id", "unknown"),
            task_description=task_description,
            system_prompt=trace_data.get("system_prompt", "N/A")[:2000],
            trace_snippet=self._extract_relevant_trace(trace_data)[:3000],
            error_output=self._extract_errors(trace_data),
            failure_types=self._failure_type_list,
        )

        # Call the judge
        try:
            # Egress Redaction: enforce safety pass immediately before LLM call
            from agent_quality.privacy.redaction import redact_text
            redacted_prompt = redact_text(prompt).value
            response = self.judge_fn(redacted_prompt)
        except Exception as e:
            return DiagnosisInfrastructureError(
                trace_id=trace_data.get("trace_id"),
                task_id=str(trace_data.get("task_id", "unknown")),
                agent_name=str(trace_data.get("agent_name", "unknown")),
                message=str(e),
                exception_type=type(e).__name__,
            )

        return self._parse_judge_response(response, trace_data)

    def _extract_relevant_trace(self, trace_data: dict[str, Any]) -> str:
        """Extract the most relevant portion of the trace for diagnosis."""
        events = trace_data.get("events", [])

        # Prioritize error events and their context
        relevant_events = []
        for i, event in enumerate(events):
            if event.get("event_type") in ["ERROR", "TOOL_CALL", "LLM_RESPONSE"]:
                # Include some context around the error
                start = max(0, i - 2)
                end = min(len(events), i + 3)
                for j in range(start, end):
                    if events[j] not in relevant_events:
                        relevant_events.append(events[j])

        # If no errors found, include last 10 events
        if not relevant_events:
            relevant_events = events[-10:]

        # Format events
        lines = []
        for event in relevant_events:
            event_type = event.get("event_type", "UNKNOWN")
            content = event.get("content", "")
            tool_name = event.get("tool_name", "")
            tool_error = event.get("tool_error", "")

            if tool_name:
                lines.append(f"[{event_type}] Tool: {tool_name}")
                if tool_error:
                    lines.append(f"  ERROR: {tool_error}")
            else:
                lines.append(f"[{event_type}] {content[:500]}")

        return "\n".join(lines)

    def _extract_errors(self, trace_data: dict[str, Any]) -> str:
        """Extract all error messages from the trace."""
        errors = []
        for event in trace_data.get("events", []):
            if event.get("event_type") == "ERROR":
                errors.append(event.get("content", ""))
            if event.get("tool_error"):
                errors.append(f"Tool {event.get('tool_name')}: {event.get('tool_error')}")
        return "\n".join(errors) if errors else "No explicit errors found"

    def _parse_judge_response(self, response: str, trace_data: dict[str, Any]) -> DiagnosisResult:
        """Parse the judge's JSON response into FailureInstance objects."""
        if not isinstance(response, str):
            return self._invalid_response(
                trace_data,
                f"Judge response must be a string, got {type(response).__name__}",
                repr(response),
            )

        # Try to extract JSON from response
        try:
            # Find JSON block
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = json.loads(response)
        except json.JSONDecodeError as e:
            return self._invalid_response(
                trace_data,
                f"Judge response was not valid JSON: {e.msg}",
                response,
            )

        if not isinstance(data, dict):
            return self._invalid_response(trace_data, "Judge response JSON must be an object", response)

        overall_score = data.get("overall_score")
        if not isinstance(overall_score, (int, float)) or not 0 <= float(overall_score) <= 10:
            return self._invalid_response(
                trace_data,
                "Judge response overall_score must be a number from 0 to 10",
                response,
            )

        failure_items = data.get("failures")
        if not isinstance(failure_items, list):
            return self._invalid_response(trace_data, "Judge response failures must be a list", response)

        failures = []
        for i, failure_data in enumerate(failure_items):
            if not isinstance(failure_data, dict):
                return self._invalid_response(
                    trace_data,
                    f"Judge response failure at index {i} must be an object",
                    response,
                )

            subcategory = failure_data.get("subcategory")
            if not isinstance(subcategory, str) or subcategory not in FAILURE_DESCRIPTIONS:
                return self._invalid_response(
                    trace_data,
                    f"Judge response failure at index {i} has unknown subcategory",
                    response,
                )

            severity = failure_data.get("severity")
            if not isinstance(severity, str) or severity.lower() not in VALID_SEVERITIES:
                return self._invalid_response(
                    trace_data,
                    f"Judge response failure at index {i} has invalid severity",
                    response,
                )

            category = self._subcategory_to_category(subcategory)

            fi = FailureInstance(
                failure_id=self._failure_id(trace_data, failure_data, i),
                task_id=str(trace_data.get("task_id", "unknown")),
                agent_name=str(trace_data.get("agent_name", "unknown")),
                model_id=trace_data.get("model_id"),
                category=category,
                subcategory=subcategory,
                description=str(failure_data.get("description", "")),
                severity=severity.lower(),
                trace_id=trace_data.get("trace_id"),
                probable_cause=str(failure_data.get("root_cause", "")),
                suggested_fix=str(failure_data.get("suggested_fix", "")),
                affected_prompt_component=failure_data.get("affected_prompt_component"),
                llm_judge_score=float(overall_score),
            )
            failures.append(fi)

        return DiagnosedFailures(failures)

    def _failure_id(self, trace_data: dict[str, Any], failure_data: dict[str, Any], index: int) -> str:
        """Build a stable failure id with a content hash to avoid unknown-trace collisions."""
        trace_part = str(trace_data.get("trace_id") or "unknown")
        payload = {
            "trace_id": trace_data.get("trace_id"),
            "task_id": trace_data.get("task_id"),
            "agent_name": trace_data.get("agent_name"),
            "index": index,
            "failure": failure_data,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:12]
        return f"diag_{trace_part}_{index}_{digest}"

    def _invalid_response(
        self,
        trace_data: dict[str, Any],
        message: str,
        response: str,
    ) -> DiagnosisInvalidResponse:
        return DiagnosisInvalidResponse(
            trace_id=trace_data.get("trace_id"),
            task_id=str(trace_data.get("task_id", "unknown")),
            agent_name=str(trace_data.get("agent_name", "unknown")),
            message=message,
            response_excerpt=response[:500],
        )

    def _subcategory_to_category(self, subcategory: str) -> str:
        """Map a subcategory to its parent category."""
        for category, subcategories in CATEGORY_GROUPS.items():
            if subcategory in subcategories:
                return category
        return "unknown"

    def _mock_judge(self, prompt: str) -> str:
        """Mock judge for testing without LLM access."""
        # Simple heuristic-based diagnosis
        prompt_lower = prompt.lower()

        failures = []

        if "syntax error" in prompt_lower or "indentation" in prompt_lower:
            failures.append({
                "subcategory": FailureCategory.CODE_SYNTAX,
                "severity": "high",
                "description": "Generated code contains syntax errors that prevent execution.",
                "root_cause": "Model produced malformed code, possibly due to insufficient examples in prompt",
                "suggested_fix": "Add syntax validation step and examples of correct code structure in system prompt",
                "affected_prompt_component": "system_prompt",
            })

        if "tool" in prompt_lower and ("not found" in prompt_lower or "error" in prompt_lower):
            failures.append({
                "subcategory": FailureCategory.SUBCATEGORY_WRONG_TOOL,
                "severity": "medium",
                "description": "Agent selected incorrect tool or provided wrong arguments.",
                "root_cause": "Tool descriptions may be ambiguous or agent lacks understanding of tool capabilities",
                "suggested_fix": "Improve tool descriptions with usage examples and expected inputs/outputs",
                "affected_prompt_component": "tool_definitions",
            })

        if "timeout" in prompt_lower:
            failures.append({
                "subcategory": FailureCategory.ENV_TIMEOUT,
                "severity": "medium",
                "description": "Task execution exceeded time limit.",
                "root_cause": "Agent may be stuck in a loop or performing inefficient operations",
                "suggested_fix": "Add step limit and early termination logic to system prompt",
                "affected_prompt_component": "system_prompt",
            })

        if not failures:
            failures.append({
                "subcategory": FailureCategory.SUBCATEGORY_NO_VALIDATION,
                "severity": "medium",
                "description": "Agent may not have properly validated its solution.",
                "root_cause": "Missing verification steps in agent's workflow",
                "suggested_fix": "Add explicit validation requirements to system prompt",
                "affected_prompt_component": "system_prompt",
            })

        return json.dumps({
            "overall_score": 4.0,
            "failures": failures,
            "summary": f"Identified {len(failures)} failure patterns in agent execution.",
        })


# -----------------------------------------------------------------------------
# Failure Clustering Engine
# -----------------------------------------------------------------------------

class FailureClusteringEngine:
    """
    Clusters failures by semantic similarity using embeddings.

    Inspired by Composo.ai's approach:
    1. Generate diagnostic descriptions (via LLM judge)
    2. Embed descriptions with task prefix for cleaner clusters
    3. Cluster in higher-dimensional space, visualize in 2D
    4. Match clusters across time by membership (Jaccard on trace IDs)
    """

    def __init__(
        self,
        embedding_fn: Callable[[list[str]], list[list[float]]] | None = None,
        min_cluster_size: int = 2,
        eps: float = 0.3,
    ):
        """
        Args:
            embedding_fn: Function that takes a list of texts and returns embeddings.
                         If None, uses TF-IDF as a fallback.
            min_cluster_size: Minimum failures to form a cluster
            eps: DBSCAN epsilon parameter
        """
        self.embedding_fn = embedding_fn or self._tfidf_embed
        self.min_cluster_size = min_cluster_size
        self.eps = eps
        self.clusters: list[FailureCluster] = []
        self._embedded_failures: list[EmbeddedFailure] = []
        self._failure_ids: set[str] = set()
        self._embeddings: np.ndarray | None = None

    def add_failures(self, failures: list[FailureInstance]) -> None:
        """Add failures to the clustering engine."""
        for f in failures:
            if f.failure_id in self._failure_ids:
                continue
            self._embedded_failures.append(self._embed_failure(f))
            self._failure_ids.add(f.failure_id)

    def cluster(self, failures: list[FailureInstance] | None = None) -> list[FailureCluster]:
        """
        Cluster failures by semantic similarity.

        Args:
            failures: Optional explicit failure collection. When supplied, the
                clustering run uses exactly this collection instead of mutable
                accumulated state.

        Returns list of FailureCluster objects.
        """
        embedded_failures = (
            [self._embed_failure(f) for f in failures]
            if failures is not None
            else self._embedded_failures
        )
        if failures is not None:
            self._embedded_failures = embedded_failures
            self._failure_ids = {item.failure.failure_id for item in embedded_failures}

        if len(embedded_failures) < self.min_cluster_size:
            self.clusters = []
            self._embeddings = None
            return []

        embedding_texts = [item.embedding_text for item in embedded_failures]

        # Generate embeddings
        embeddings = self.embedding_fn(embedding_texts)
        self._embeddings = np.array(embeddings)
        for embedded_failure, embedding in zip(embedded_failures, embeddings):
            embedded_failure.failure.embedding = list(embedding)

        # Cluster using HDBSCAN or DBSCAN
        if len(embedded_failures) >= 10:
            try:
                clusterer = HDBSCAN(min_cluster_size=self.min_cluster_size, metric="euclidean")
                labels = clusterer.fit_predict(self._embeddings)
            except ImportError:
                clusterer = DBSCAN(eps=self.eps, min_samples=self.min_cluster_size, metric="cosine")
                labels = clusterer.fit_predict(self._embeddings)
        else:
            clusterer = DBSCAN(eps=self.eps, min_samples=self.min_cluster_size, metric="cosine")
            labels = clusterer.fit_predict(self._embeddings)

        # Group failures by cluster
        cluster_groups: dict[int, list[tuple[int, FailureInstance]]] = defaultdict(list)
        for i, (label, embedded_failure) in enumerate(zip(labels, embedded_failures)):
            if label >= 0:  # -1 is noise
                cluster_groups[int(label)].append((i, embedded_failure.failure))

        # Build FailureCluster objects
        self.clusters = []
        for cluster_id, items in cluster_groups.items():
            indices, failures = zip(*items)
            cluster = self._build_cluster(cluster_id, list(failures), list(indices))
            self.clusters.append(cluster)

        return self.clusters

    def _get_all_failures(self) -> list[FailureInstance]:
        """Get all failure instances that were added."""
        return [item.failure for item in self._embedded_failures]

    def _embed_failure(self, failure: FailureInstance) -> EmbeddedFailure:
        """Create the embedding text and keep it attached to the source failure."""
        text = f"Task: {failure.task_id}. Failure: {failure.description}. Error: {failure.error_message}"
        return EmbeddedFailure(failure=failure, embedding_text=text)

    def _build_cluster(self, cluster_id: int, failures: list[FailureInstance], indices: list[int]) -> FailureCluster:
        """Build a FailureCluster from grouped failures."""
        # Determine dominant category
        category_counts = Counter(f.category for f in failures if f.category)
        subcategory_counts = Counter(f.subcategory for f in failures if f.subcategory)

        dominant_category = category_counts.most_common(1)[0][0] if category_counts else None
        dominant_subcategory = subcategory_counts.most_common(1)[0][0] if subcategory_counts else None

        # Extract common keywords from descriptions
        all_descriptions = " ".join(f.description for f in failures)
        keywords = self._extract_keywords(all_descriptions)

        # Extract common tool calls
        tool_calls = []
        for f in failures:
            if f.trace_snippet:
                tools = re.findall(r'Tool:\s*(\w+)', f.trace_snippet)
                tool_calls.extend(tools)
        common_tools = [tool for tool, count in Counter(tool_calls).most_common(5) if count > 1]

        # Calculate average severity
        severity_scores = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        avg_sev_score = sum(severity_scores.get(f.severity, 2) for f in failures) / len(failures)
        severity_map = {1: "low", 2: "medium", 3: "high", 4: "critical"}
        avg_severity = severity_map.get(round(avg_sev_score), "medium")

        # Generate cluster label
        label = self._generate_cluster_label(dominant_subcategory, keywords, len(failures))

        # Generate suggestions
        prompt_fix = self._generate_prompt_fix(dominant_subcategory, failures)
        tool_fix = self._generate_tool_fix(dominant_subcategory, failures)

        return FailureCluster(
            cluster_id=cluster_id,
            label=label,
            description=f"Cluster of {len(failures)} failures: {dominant_subcategory or 'mixed'}",
            failures=failures,
            dominant_category=dominant_category,
            dominant_subcategory=dominant_subcategory,
            affected_agents=set(f.agent_name for f in failures),
            affected_models=set(f.model_id for f in failures if f.model_id),
            common_keywords=keywords[:10],
            common_tool_calls=common_tools,
            avg_severity=avg_severity,
            suggested_prompt_fix=prompt_fix,
            suggested_tool_fix=tool_fix,
        )

    def _generate_cluster_label(self, subcategory: str | None, keywords: list[str], count: int) -> str:
        """Generate a human-readable label for the cluster."""
        if subcategory and subcategory in FAILURE_DESCRIPTIONS:
            base = FAILURE_DESCRIPTIONS[subcategory]
        elif keywords:
            base = f"{keywords[0].title()} Issues"
        else:
            base = "Unknown Failure Pattern"

        return f"{base} (n={count})"

    def _generate_prompt_fix(self, subcategory: str | None, failures: list[FailureInstance]) -> str:
        """Generate a prompt fix suggestion based on the failure pattern."""
        prompt_fixes = {
            FailureCategory.SUBCATEGORY_DISOBEY_SPEC: "Add explicit step-by-step instructions and constraint checklist to system prompt",
            FailureCategory.SUBCATEGORY_WRONG_TOOL: "Improve tool descriptions with usage examples and clarify when to use each tool",
            FailureCategory.SUBCATEGORY_WRONG_ARGS: "Add JSON schema validation and examples of correct tool arguments",
            FailureCategory.SUBCATEGORY_PREMATURE_STOP: "Add explicit completion criteria and require verification before stopping",
            FailureCategory.SUBCATEGORY_NO_VALIDATION: "Require agent to run tests and verify output before completing",
            FailureCategory.CODE_SYNTAX: "Add syntax validation step and require compilation before submission",
            FailureCategory.LLM_HALLUCINATION: "Add grounding requirements - agent must cite specific APIs and verify existence",
        }
        return prompt_fixes.get(subcategory or "", "Review system prompt for clarity and completeness")

    def _generate_tool_fix(self, subcategory: str | None, failures: list[FailureInstance]) -> str:
        """Generate a tool fix suggestion."""
        tool_fixes = {
            FailureCategory.SUBCATEGORY_WRONG_TOOL: "Review and enhance tool descriptions with clearer use cases",
            FailureCategory.SUBCATEGORY_TOOL_NOT_FOUND: "Ensure all referenced tools are properly registered and available",
            FailureCategory.SUBCATEGORY_REPEATED_TOOL_ERRORS: "Add tool error handling and fallback mechanisms",
        }
        return tool_fixes.get(subcategory or "", "No specific tool changes needed")

    def _extract_keywords(self, text: str, top_n: int = 15) -> list[str]:
        """Extract important keywords from failure descriptions."""
        # Simple TF-IDF based keyword extraction
        vectorizer = TfidfVectorizer(
            max_features=100,
            stop_words="english",
            ngram_range=(1, 2),
        )
        try:
            tfidf = vectorizer.fit_transform([text])
            feature_names = vectorizer.get_feature_names_out()
            scores = tfidf.toarray()[0]
            top_indices = scores.argsort()[-top_n:][::-1]
            return [feature_names[i] for i in top_indices if scores[i] > 0]
        except Exception:
            return []

    def _tfidf_embed(self, texts: list[str]) -> list[list[float]]:
        """Generate TF-IDF embeddings as fallback."""
        vectorizer = TfidfVectorizer(max_features=128, stop_words="english")
        try:
            embeddings = vectorizer.fit_transform(texts).toarray()
            return embeddings.tolist()
        except Exception:
            return [[0.0] * 128 for _ in texts]

    def compare_with_previous(
        self,
        previous_clusters: list[FailureCluster],
        trace_id_field: str = "trace_id",
    ) -> dict[str, Any]:
        """
        Compare current clusters with previous run to detect drift.

        Uses Jaccard similarity on trace IDs for robust matching.
        """
        matches = []
        new_clusters = []
        resolved_clusters = []

        current_trace_sets = {c.cluster_id: self._cluster_trace_ids(c) for c in self.clusters}
        previous_trace_sets = {c.cluster_id: self._cluster_trace_ids(c) for c in previous_clusters}

        # Find matches
        for curr_id, curr_traces in current_trace_sets.items():
            best_match = None
            best_jaccard = 0.0

            for prev_id, prev_traces in previous_trace_sets.items():
                intersection = len(curr_traces & prev_traces)
                union = len(curr_traces | prev_traces)
                jaccard = intersection / union if union > 0 else 0

                if jaccard > best_jaccard and jaccard > 0.3:  # Threshold for "same cluster"
                    best_jaccard = jaccard
                    best_match = prev_id

            if best_match is not None:
                curr_cluster = next(c for c in self.clusters if c.cluster_id == curr_id)
                prev_cluster = next(c for c in previous_clusters if c.cluster_id == best_match)
                matches.append({
                    "current_cluster_id": curr_id,
                    "previous_cluster_id": best_match,
                    "jaccard": best_jaccard,
                    "previous_count": len(prev_cluster.failures),
                    "current_count": len(curr_cluster.failures),
                    "trend": "growing" if len(curr_cluster.failures) > len(prev_cluster.failures) else "shrinking",
                })
            else:
                new_clusters.append(curr_id)

        # Find resolved clusters (in previous but not in current)
        matched_prev_ids = set(m["previous_cluster_id"] for m in matches)
        for prev_id in previous_trace_sets:
            if prev_id not in matched_prev_ids:
                resolved_clusters.append(prev_id)

        return {
            "matched_clusters": matches,
            "new_clusters": new_clusters,
            "resolved_clusters": resolved_clusters,
            "total_current": len(self.clusters),
            "total_previous": len(previous_clusters),
        }

    def _cluster_trace_ids(self, cluster: FailureCluster) -> set[str]:
        """Return only real trace IDs; missing IDs cannot establish cluster continuity."""
        return {f.trace_id for f in cluster.failures if f.trace_id is not None}


# -----------------------------------------------------------------------------
# Root Cause Analysis Engine
# -----------------------------------------------------------------------------

class RootCauseAnalyzer:
    """
    Deep root cause analysis for identified failure clusters.

    Goes beyond surface-level classification to identify:
    - Which prompt components are responsible
    - Whether the issue is model-specific or agent-agnostic
    - The minimal fix needed
    """

    def __init__(self, llm_fn: Callable[[str], str] | None = None):
        self.llm_fn = llm_fn

    def analyze_cluster(self, cluster: FailureCluster) -> dict[str, Any]:
        """Perform deep RCA on a failure cluster."""
        analysis = {
            "cluster_id": cluster.cluster_id,
            "cluster_label": cluster.label,
            "failure_count": len(cluster.failures),
        }

        # 1. Prompt component analysis
        analysis["prompt_component_analysis"] = self._analyze_prompt_components(cluster)

        # 2. Model vs agent analysis
        analysis["model_agent_breakdown"] = self._analyze_model_agent_distribution(cluster)

        # 3. Temporal pattern
        analysis["temporal_pattern"] = self._analyze_temporal_pattern(cluster)

        # 4. Minimal fix recommendation
        analysis["minimal_fix"] = self._recommend_minimal_fix(cluster)

        # 5. Regression test specification
        analysis["regression_tests"] = self._specify_regression_tests(cluster)

        return analysis

    def _analyze_prompt_components(self, cluster: FailureCluster) -> dict[str, Any]:
        """Analyze which prompt components are most associated with failures."""
        component_counts = Counter()
        for f in cluster.failures:
            if f.affected_prompt_component:
                component_counts[f.affected_prompt_component] += 1

        total = len(cluster.failures)
        return {
            "component_distribution": {
                comp: {"count": count, "percentage": count / total * 100}
                for comp, count in component_counts.most_common()
            },
            "primary_component": component_counts.most_common(1)[0][0] if component_counts else "unknown",
        }

    def _analyze_model_agent_distribution(self, cluster: FailureCluster) -> dict[str, Any]:
        """Analyze whether failures are model-specific or agent-agnostic."""
        agent_counts = Counter(f.agent_name for f in cluster.failures)
        model_counts = Counter(f.model_id for f in cluster.failures if f.model_id)

        total = len(cluster.failures)
        agent_concentration = max(agent_counts.values()) / total if agent_counts else 0
        model_concentration = max(model_counts.values()) / total if model_counts else 0

        return {
            "affected_agents": dict(agent_counts),
            "affected_models": dict(model_counts),
            "agent_concentration": agent_concentration,
            "model_concentration": model_concentration,
            "is_agent_specific": agent_concentration > 0.7,
            "is_model_specific": model_concentration > 0.7,
            "is_systemic": agent_concentration < 0.5 and model_concentration < 0.5,
        }

    def _analyze_temporal_pattern(self, cluster: FailureCluster) -> dict[str, Any]:
        """Analyze if failures are clustered in time (suggesting a specific change caused them)."""
        timestamps = [f.timestamp for f in cluster.failures]
        if len(timestamps) < 2:
            return {"pattern": "insufficient_data"}

        timestamps.sort()
        gaps = [(timestamps[i+1] - timestamps[i]).total_seconds() / 3600
                for i in range(len(timestamps) - 1)]

        avg_gap = sum(gaps) / len(gaps) if gaps else 0
        max_gap = max(gaps) if gaps else 0

        # If failures are clustered (large gap after initial cluster), suggests a change caused them
        is_burst = max_gap > avg_gap * 3 if avg_gap > 0 else False

        return {
            "first_occurrence": timestamps[0].isoformat(),
            "last_occurrence": timestamps[-1].isoformat(),
            "avg_gap_hours": avg_gap,
            "max_gap_hours": max_gap,
            "is_burst_pattern": is_burst,
            "pattern": "burst" if is_burst else "continuous",
        }

    def _recommend_minimal_fix(self, cluster: FailureCluster) -> dict[str, Any]:
        """Recommend the smallest change that would address this cluster."""
        if cluster.dominant_subcategory in [
            FailureCategory.SUBCATEGORY_WRONG_TOOL,
            FailureCategory.SUBCATEGORY_WRONG_ARGS,
        ]:
            return {
                "fix_type": "tool_improvement",
                "description": "Improve tool definitions and add validation",
                "estimated_effort": "small",
                "confidence": "high",
            }
        elif cluster.dominant_subcategory in [
            FailureCategory.SUBCATEGORY_DISOBEY_SPEC,
            FailureCategory.SUBCATEGORY_NO_TERMINATION,
        ]:
            return {
                "fix_type": "prompt_enhancement",
                "description": "Add explicit constraints and completion criteria to system prompt",
                "estimated_effort": "small",
                "confidence": "high",
            }
        elif cluster.dominant_subcategory in [
            FailureCategory.CODE_SYNTAX,
            FailureCategory.CODE_LOGIC,
        ]:
            return {
                "fix_type": "workflow_improvement",
                "description": "Add code validation and test execution steps",
                "estimated_effort": "medium",
                "confidence": "medium",
            }
        else:
            return {
                "fix_type": "investigation_needed",
                "description": "Requires deeper investigation to determine minimal fix",
                "estimated_effort": "large",
                "confidence": "low",
            }

    def _specify_regression_tests(self, cluster: FailureCluster) -> list[dict[str, Any]]:
        """Generate regression test specifications for this cluster."""
        tests = []

        # Create a regression test based on the failure pattern
        for i, failure in enumerate(cluster.failures[:3]):  # Top 3 representative failures
            tests.append({
                "test_id": f"regression_{cluster.cluster_id}_{i}",
                "description": f"Verify fix for: {failure.description[:100]}",
                "trigger_condition": failure.probable_cause,
                "verification_method": "Execute task and verify no failure occurs",
                "priority": "high" if failure.severity in ["high", "critical"] else "medium",
            })

        return tests


# -----------------------------------------------------------------------------
# Main Failure Analysis Pipeline
# -----------------------------------------------------------------------------

class FailureAnalysisPipeline:
    """
    End-to-end pipeline for analyzing agent failures.

    Orchestrates:
    1. LLM-based diagnosis of individual traces
    2. Embedding-based clustering of similar failures
    3. Root cause analysis of clusters
    4. Comparison with previous runs
    5. Actionable fix recommendations
    """

    def __init__(
        self,
        diagnoser: LLMJudgeDiagnoser | None = None,
        clusterer: FailureClusteringEngine | None = None,
        rca_engine: RootCauseAnalyzer | None = None,
    ):
        if diagnoser is None:
            raise ValueError(
                "FailureAnalysisPipeline requires an explicit diagnoser. "
                "Use LLMJudgeDiagnoser(judge_fn=...) for runtime analysis or "
                "LLMJudgeDiagnoser(use_mock_judge=True) for tests/demos."
            )
        self.diagnoser = diagnoser
        self.clusterer = clusterer or FailureClusteringEngine()
        self.rca_engine = rca_engine or RootCauseAnalyzer()

        self.all_failures: list[FailureInstance] = []
        self.diagnosis_errors: list[DiagnosisError] = []
        self.previous_clusters: list[FailureCluster] | None = None
        self._clustering_has_run = False

    def process_traces(self, traces: list[dict[str, Any]]) -> list[FailureInstance]:
        """
        Process a batch of execution traces and extract failures.

        Args:
            traces: List of trace dictionaries from the telemetry system

        Returns:
            List of diagnosed FailureInstance objects
        """
        all_failures = []

        for trace in traces:
            result = self.diagnoser.diagnose(trace)
            if isinstance(result, DiagnosedFailures):
                all_failures.extend(result.failures)
            else:
                self.diagnosis_errors.append(result)

        self.all_failures.extend(all_failures)
        return all_failures

    def run_clustering(self) -> list[FailureCluster]:
        """Cluster all diagnosed failures."""
        clusters = self.clusterer.cluster(self.all_failures)
        self._clustering_has_run = True
        return clusters

    def generate_report(self) -> dict[str, Any]:
        """Generate comprehensive failure analysis report."""
        if self.all_failures and not self._clustering_has_run:
            raise RuntimeError("run_clustering() must be called before generate_report().")

        clusters = self.clusterer.clusters

        # Run RCA on each cluster
        cluster_analyses = []
        for cluster in clusters:
            analysis = self.rca_engine.analyze_cluster(cluster)
            cluster_analyses.append(analysis)

        # Overall statistics
        category_distribution = Counter(f.category for f in self.all_failures if f.category)
        severity_distribution = Counter(f.severity for f in self.all_failures)

        # Compare with previous if available
        comparison = None
        if self.previous_clusters is not None:
            comparison = self.clusterer.compare_with_previous(self.previous_clusters)

        return {
            "summary": {
                "total_failures_diagnosed": len(self.all_failures),
                "diagnosis_errors": len(self.diagnosis_errors),
                "clusters_identified": len(clusters),
                "category_distribution": dict(category_distribution),
                "severity_distribution": dict(severity_distribution),
            },
            "clusters": [c.to_dict() for c in clusters],
            "cluster_analyses": cluster_analyses,
            "drift_comparison": comparison,
            "top_recommendations": self._generate_top_recommendations(clusters),
        }

    def _generate_top_recommendations(self, clusters: list[FailureCluster]) -> list[dict[str, Any]]:
        """Generate prioritized list of fix recommendations."""
        recommendations = []

        for cluster in sorted(clusters, key=lambda c: len(c.failures), reverse=True)[:5]:
            recommendations.append({
                "priority": len(cluster.failures),
                "cluster_label": cluster.label,
                "failure_count": len(cluster.failures),
                "affected_agents": list(cluster.affected_agents),
                "suggested_fix": cluster.suggested_prompt_fix or cluster.suggested_tool_fix,
                "fix_target": "prompt" if cluster.suggested_prompt_fix else "tool",
                "estimated_effort": "small" if len(cluster.failures) < 5 else "medium",
            })

        return recommendations

    def save_state(self, filepath: str) -> None:
        """Save the current analysis state to disk."""
        state = {
            "schema_version": ANALYSIS_STATE_SCHEMA_VERSION,
            "failures": [f.to_dict() for f in self.all_failures],
            "clusters": [self._cluster_to_state_dict(c) for c in self.clusterer.clusters],
            "diagnosis_errors": [error.to_dict() for error in self.diagnosis_errors],
            "timestamp": utc_now().isoformat(),
        }
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.tmp")
        with open(temp_path, "w") as f:
            json.dump(state, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        temp_path.replace(path)

    def load_state(self, filepath: str) -> None:
        """Load a previous analysis state."""
        with open(filepath) as f:
            state = json.load(f)

        if state.get("schema_version") not in (None, ANALYSIS_STATE_SCHEMA_VERSION):
            raise ValueError(f"Unsupported analysis state schema_version: {state.get('schema_version')}")

        self.all_failures = [
            FailureInstance.from_dict(failure_data)
            for failure_data in state.get("failures", [])
        ]
        failure_lookup = {failure.failure_id: failure for failure in self.all_failures}

        loaded_clusters = [
            self._cluster_from_state_dict(cluster_data, failure_lookup)
            for cluster_data in state.get("clusters", [])
        ]
        self.clusterer.clusters = loaded_clusters
        self.previous_clusters = loaded_clusters
        self._clustering_has_run = True
        self.diagnosis_errors = [
            self._diagnosis_error_from_state_dict(error_data)
            for error_data in state.get("diagnosis_errors", [])
        ]

    def _cluster_to_state_dict(self, cluster: FailureCluster) -> dict[str, Any]:
        state = cluster.to_dict()
        state["failure_ids"] = [failure.failure_id for failure in cluster.failures]
        return state

    def _cluster_from_state_dict(
        self,
        data: dict[str, Any],
        failure_lookup: dict[str, FailureInstance],
    ) -> FailureCluster:
        failure_ids = data.get("failure_ids", [])
        failures = [
            failure_lookup[failure_id]
            for failure_id in failure_ids
            if failure_id in failure_lookup
        ]

        return FailureCluster(
            cluster_id=int(data.get("cluster_id", 0)),
            label=str(data.get("label", "Unknown Failure Pattern")),
            description=str(data.get("description", "")),
            failures=failures,
            dominant_category=data.get("dominant_category"),
            dominant_subcategory=data.get("dominant_subcategory"),
            affected_agents=set(data.get("affected_agents", [])),
            affected_models=set(data.get("affected_models", [])),
            common_keywords=list(data.get("common_keywords", [])),
            common_tool_calls=list(data.get("common_tool_calls", [])),
            avg_severity=str(data.get("avg_severity", "medium")),
            suggested_prompt_fix=str(data.get("suggested_prompt_fix", "")),
            suggested_tool_fix=str(data.get("suggested_tool_fix", "")),
            regression_tests_needed=list(data.get("regression_tests_needed", [])),
        )

    def _diagnosis_error_from_state_dict(self, data: dict[str, Any]) -> DiagnosisError:
        error_type = data.get("type")
        if error_type == "infrastructure_error":
            return DiagnosisInfrastructureError(
                trace_id=data.get("trace_id"),
                task_id=str(data.get("task_id", "unknown")),
                agent_name=str(data.get("agent_name", "unknown")),
                message=str(data.get("message", "")),
                exception_type=str(data.get("exception_type", "Exception")),
            )

        return DiagnosisInvalidResponse(
            trace_id=data.get("trace_id"),
            task_id=str(data.get("task_id", "unknown")),
            agent_name=str(data.get("agent_name", "unknown")),
            message=str(data.get("message", "")),
            response_excerpt=str(data.get("response_excerpt", "")),
        )
