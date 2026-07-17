"""Domain models for benchmark tasks, outputs, and results."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any

# -----------------------------------------------------------------------------
# Core Types
# -----------------------------------------------------------------------------

class TaskType(Enum):
    """Categories of coding tasks for benchmarking."""
    CODE_GENERATION = auto()      # Generate function/class from spec
    BUG_FIXING = auto()           # Fix reported issue
    REFACTORING = auto()          # Restructure code
    TEST_GENERATION = auto()      # Write tests for existing code
    CODE_REVIEW = auto()          # Review PR for issues
    DOCUMENTATION = auto()        # Generate docs
    DEBUGGING = auto()            # Diagnose and fix runtime errors
    API_INTEGRATION = auto()      # Integrate external APIs
    DEPENDENCY_MANAGEMENT = auto() # Handle package/dependency issues
    TERMINAL_WORKFLOW = auto()    # Multi-step CLI operations


class Difficulty(Enum):
    """Task difficulty levels."""
    EASY = 1
    MEDIUM = 2
    HARD = 3
    EXPERT = 4


@dataclass(frozen=True)
class TaskId:
    """Unique identifier for a benchmark task."""
    namespace: str  # e.g., "swe-bench", "custom", "terminal-bench"
    name: str       # e.g., "django-1234", "fibonacci-generator"
    version: str = "1.0"

    def __str__(self) -> str:
        return f"{self.namespace}::{self.name}@v{self.version}"

    @property
    def stable_id(self) -> str:
        """Deterministic hash for stable referencing."""
        return hashlib.sha256(str(self).encode()).hexdigest()[:16]

    @classmethod
    def from_string(cls, value: str) -> TaskId:
        """Parse the stable ``namespace::name@vversion`` representation."""
        namespace, separator, name_version = value.partition("::")
        if not separator:
            return cls(namespace=value, name="unknown")
        name, version_separator, version = name_version.rpartition("@v")
        if not version_separator:
            return cls(namespace=namespace, name=name_version)
        return cls(namespace=namespace, name=name, version=version)


@dataclass
class BenchmarkTask:
    """
    A single benchmark task for evaluating coding agents.

    A task encapsulates everything needed to evaluate an agent:
    - The problem description/prompt
    - Setup code and context
    - Evaluation criteria and test cases
    - Metadata for categorization and analysis
    """
    task_id: TaskId
    task_type: TaskType
    difficulty: Difficulty

    # Problem specification
    instruction: str                          # What the agent should do
    context_files: dict[str, str] = field(default_factory=dict)  # filename -> content
    setup_commands: list[str] = field(default_factory=list)      # Commands to run before

    # Evaluation
    test_cases: list[TestCase] = field(default_factory=list)
    evaluation_script: str | None = None      # Optional custom evaluator
    success_criteria: list[str] = field(default_factory=list)   # Natural language criteria

    # Metadata
    tags: list[str] = field(default_factory=list)
    estimated_duration_sec: int = 60
    language: str = "python"
    source_url: str | None = None             # Link to original issue/PR

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": str(self.task_id),
            "task_type": self.task_type.name,
            "difficulty": self.difficulty.name,
            "instruction": self.instruction,
            "context_files": self.context_files,
            "setup_commands": self.setup_commands,
            "test_cases": [tc.to_dict() for tc in self.test_cases],
            "evaluation_script": self.evaluation_script,
            "success_criteria": self.success_criteria,
            "tags": self.tags,
            "estimated_duration_sec": self.estimated_duration_sec,
            "language": self.language,
            "source_url": self.source_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BenchmarkTask:
        """Reconstruct a BenchmarkTask from its dictionary representation."""
        return cls(
            task_id=TaskId.from_string(str(data["task_id"])),
            task_type=TaskType[data["task_type"]],
            difficulty=Difficulty[data["difficulty"]],
            instruction=data["instruction"],
            context_files=data.get("context_files", {}),
            setup_commands=data.get("setup_commands", []),
            test_cases=[TestCase.from_dict(tc) for tc in data.get("test_cases", [])],
            evaluation_script=data.get("evaluation_script"),
            success_criteria=data.get("success_criteria", []),
            tags=data.get("tags", []),
            estimated_duration_sec=data.get("estimated_duration_sec", 60),
            language=data.get("language", "python"),
            source_url=data.get("source_url"),
        )


@dataclass
class TestCase:
    """An individual test for verifying agent output."""
    name: str
    test_type: str  # "unit", "integration", "behavioral", "llm_judge"

    # For programmatic tests
    input_data: dict[str, Any] | None = None
    expected_output: Any | None = None
    expected_behavior: str | None = None  # Natural language description

    # For LLM-as-judge tests
    evaluation_prompt: str | None = None

    # Scoring
    weight: float = 1.0  # Relative importance
    partial_credit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestCase:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class AgentOutput:
    """
    Structured capture of everything an agent produced during task execution.

    This is the primary artifact for evaluation, clustering, and analysis.
    """
    task_id: TaskId
    agent_name: str           # e.g., "claude-code", "codex-cli", "openhands"
    model_id: str | None = None  # e.g., "claude-sonnet-4", "gpt-4o"

    # Execution artifacts
    final_code: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    execution_trajectory: list[TrajectoryStep] = field(default_factory=list)

    # Timing and resources
    start_time: datetime = field(default_factory=datetime.utcnow)
    end_time: datetime | None = None
    total_tokens: int = 0
    cost_usd: float = 0.0

    # Raw capture
    raw_messages: list[dict[str, Any]] = field(default_factory=list)
    system_prompt: str | None = None
    environment_state: dict[str, Any] = field(default_factory=dict)

    # Human verification
    human_verified: bool = False
    human_correct: bool | None = None       # None = not verified, True/False = judgment
    human_notes: str = ""

    @property
    def duration_sec(self) -> float:
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_calls)

    @property
    def step_count(self) -> int:
        return len(self.execution_trajectory)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": str(self.task_id),
            "agent_name": self.agent_name,
            "model_id": self.model_id,
            "final_code": self.final_code,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "execution_trajectory": [step.to_dict() for step in self.execution_trajectory],
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "raw_messages": self.raw_messages,
            "system_prompt": self.system_prompt,
            "environment_state": self.environment_state,
            "human_verified": self.human_verified,
            "human_correct": self.human_correct,
            "human_notes": self.human_notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentOutput:
        """Reconstruct captured agent output from its JSON representation."""
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        return cls(
            task_id=TaskId.from_string(str(data["task_id"])),
            agent_name=str(data["agent_name"]),
            model_id=data.get("model_id"),
            final_code=data.get("final_code"),
            tool_calls=[ToolCall.from_dict(item) for item in data.get("tool_calls", [])],
            execution_trajectory=[
                TrajectoryStep.from_dict(item)
                for item in data.get("execution_trajectory", [])
            ],
            start_time=(
                datetime.fromisoformat(start_time)
                if isinstance(start_time, str)
                else datetime.utcnow()
            ),
            end_time=datetime.fromisoformat(end_time) if isinstance(end_time, str) else None,
            total_tokens=int(data.get("total_tokens", 0)),
            cost_usd=float(data.get("cost_usd", 0.0)),
            raw_messages=list(data.get("raw_messages", [])),
            system_prompt=data.get("system_prompt"),
            environment_state=dict(data.get("environment_state", {})),
            human_verified=bool(data.get("human_verified", False)),
            human_correct=data.get("human_correct"),
            human_notes=str(data.get("human_notes", "")),
        )


@dataclass
class ToolCall:
    """A single tool invocation captured during agent execution."""
    tool_name: str
    arguments: dict[str, Any]
    result: Any | None = None
    error: str | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result": self.result,
            "error": self.error,
            "timestamp": self.timestamp.isoformat(),
            "latency_ms": self.latency_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolCall:
        """Reconstruct a tool call from serialized output."""
        timestamp = data.get("timestamp")
        return cls(
            tool_name=str(data["tool_name"]),
            arguments=dict(data.get("arguments", {})),
            result=data.get("result"),
            error=data.get("error"),
            timestamp=(
                datetime.fromisoformat(timestamp)
                if isinstance(timestamp, str)
                else datetime.utcnow()
            ),
            latency_ms=float(data.get("latency_ms", 0.0)),
        )


@dataclass
class TrajectoryStep:
    """A single step in the agent's execution trajectory."""
    step_number: int
    step_type: str  # "thought", "action", "observation", "error", "completion"
    content: str
    tool_call: ToolCall | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    tokens_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_number": self.step_number,
            "step_type": self.step_type,
            "content": self.content[:500] if len(self.content) > 500 else self.content,
            "tool_call": self.tool_call.to_dict() if self.tool_call else None,
            "timestamp": self.timestamp.isoformat(),
            "tokens_used": self.tokens_used,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrajectoryStep:
        """Reconstruct one trajectory step from serialized output."""
        timestamp = data.get("timestamp")
        tool_call = data.get("tool_call")
        return cls(
            step_number=int(data["step_number"]),
            step_type=str(data["step_type"]),
            content=str(data.get("content", "")),
            tool_call=ToolCall.from_dict(tool_call) if isinstance(tool_call, dict) else None,
            timestamp=(
                datetime.fromisoformat(timestamp)
                if isinstance(timestamp, str)
                else datetime.utcnow()
            ),
            tokens_used=int(data.get("tokens_used", 0)),
        )


@dataclass
class EvaluationResult:
    """Result of evaluating an agent's output on a benchmark task."""
    task_id: TaskId
    agent_name: str

    # Overall score
    passed: bool = False
    score: float = 0.0  # 0.0 to 1.0

    model_id: str | None = None

    # Test-level results
    test_results: list[TestResult] = field(default_factory=list)

    # Failure analysis (populated when passed=False)
    failure_category: str | None = None
    failure_description: str | None = None
    root_cause: str | None = None

    # Metadata
    evaluation_timestamp: datetime = field(default_factory=datetime.utcnow)
    evaluator_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": str(self.task_id),
            "agent_name": self.agent_name,
            "model_id": self.model_id,
            "passed": self.passed,
            "score": self.score,
            "test_results": [tr.to_dict() for tr in self.test_results],
            "failure_category": self.failure_category,
            "failure_description": self.failure_description,
            "root_cause": self.root_cause,
            "evaluation_timestamp": self.evaluation_timestamp.isoformat(),
            "evaluator_version": self.evaluator_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluationResult:
        """Reconstruct an evaluation result, including typed test results."""
        timestamp = data.get("evaluation_timestamp")
        return cls(
            task_id=TaskId.from_string(str(data["task_id"])),
            agent_name=str(data["agent_name"]),
            model_id=data.get("model_id"),
            passed=bool(data.get("passed", False)),
            score=float(data.get("score", 0.0)),
            test_results=[TestResult.from_dict(item) for item in data.get("test_results", [])],
            failure_category=data.get("failure_category"),
            failure_description=data.get("failure_description"),
            root_cause=data.get("root_cause"),
            evaluation_timestamp=(
                datetime.fromisoformat(timestamp)
                if isinstance(timestamp, str)
                else datetime.utcnow()
            ),
            evaluator_version=str(data.get("evaluator_version", "1.0")),
        )


@dataclass
class TestResult:
    """Result of a single test case."""
    test_name: str
    passed: bool
    score: float
    details: str = ""
    execution_time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestResult:
        """Reconstruct an individual evaluator result."""
        return cls(
            test_name=str(data["test_name"]),
            passed=bool(data.get("passed", False)),
            score=float(data.get("score", 0.0)),
            details=str(data.get("details", "")),
            execution_time_ms=float(data.get("execution_time_ms", 0.0)),
        )
