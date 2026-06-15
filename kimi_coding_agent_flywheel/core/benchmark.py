"""
Core Benchmark Framework for Coding Agent Quality Flywheel.

This module provides the foundational types and interfaces for defining,
executing, and scoring benchmark tasks across different coding agents.
Inspired by SWE-bench, Terminal-Bench, and EvalPlus methodologies.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Generic, TypeVar

import numpy as np


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
        tid_parts = data["task_id"].split("::")
        ns = tid_parts[0]
        name_ver = tid_parts[1] if len(tid_parts) > 1 else "unknown@v1.0"
        name, version = name_ver.split("@v") if "@v" in name_ver else (name_ver, "1.0")

        return cls(
            task_id=TaskId(namespace=ns, name=name, version=version),
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


# -----------------------------------------------------------------------------
# Agent Interface
# -----------------------------------------------------------------------------

class CodingAgent(ABC):
    """
    Abstract interface for coding agents to be evaluated.

    Implement this for each agent you want to benchmark:
    - Claude Code
    - Codex CLI
    - OpenHands
    - Custom agents
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable agent identifier."""
        pass

    @property
    @abstractmethod
    def model_id(self) -> str | None:
        """The underlying LLM model, if applicable."""
        pass

    @abstractmethod
    async def execute(self, task: BenchmarkTask) -> AgentOutput:
        """
        Execute the agent on a benchmark task and return structured output.

        Implementations MUST capture:
        - All tool calls with arguments and results
        - The full execution trajectory
        - Token usage and cost
        - Final code/output
        """
        pass

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the current system prompt for this agent."""
        pass

    @abstractmethod
    def update_system_prompt(self, new_prompt: str) -> None:
        """Update the system prompt (for prompt optimization experiments)."""
        pass


# -----------------------------------------------------------------------------
# Benchmark Suite
# -----------------------------------------------------------------------------

class BenchmarkSuite:
    """
    A collection of benchmark tasks with execution and evaluation capabilities.

    This is the primary interface for running evaluations and collecting
    results for the quality flywheel.
    """

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.tasks: dict[str, BenchmarkTask] = {}
        self.results: list[EvaluationResult] = []
        self._evaluators: dict[str, Evaluator] = {}

    def add_task(self, task: BenchmarkTask) -> None:
        """Add a task to the suite."""
        self.tasks[str(task.task_id)] = task

    def add_tasks(self, tasks: list[BenchmarkTask]) -> None:
        for task in tasks:
            self.add_task(task)

    def register_evaluator(self, task_type: TaskType, evaluator: Evaluator) -> None:
        """Register an evaluator for a specific task type."""
        self._evaluators[task_type.name] = evaluator

    def get_tasks_by_type(self, task_type: TaskType) -> list[BenchmarkTask]:
        return [t for t in self.tasks.values() if t.task_type == task_type]

    def get_tasks_by_difficulty(self, difficulty: Difficulty) -> list[BenchmarkTask]:
        return [t for t in self.tasks.values() if t.difficulty == difficulty]

    def get_tasks_by_tag(self, tag: str) -> list[BenchmarkTask]:
        return [t for t in self.tasks.values() if tag in t.tags]

    async def run_evaluation(
        self,
        agent: CodingAgent,
        task_filter: Callable[[BenchmarkTask], bool] | None = None,
        max_concurrent: int = 4,
        timeout_per_task: int = 300,
    ) -> list[EvaluationResult]:
        """
        Run the full benchmark suite against an agent.

        Args:
            agent: The coding agent to evaluate
            task_filter: Optional filter function for selecting tasks
            max_concurrent: Maximum parallel evaluations
            timeout_per_task: Seconds before aborting a task

        Returns:
            List of evaluation results
        """
        tasks = list(self.tasks.values())
        if task_filter:
            tasks = [t for t in tasks if task_filter(t)]

        semaphore = asyncio.Semaphore(max_concurrent)
        results: list[EvaluationResult] = []

        async def _run_single(task: BenchmarkTask) -> EvaluationResult:
            async with semaphore:
                try:
                    return await asyncio.wait_for(
                        self._evaluate_single(agent, task),
                        timeout=timeout_per_task,
                    )
                except asyncio.TimeoutError:
                    return EvaluationResult(
                        task_id=task.task_id,
                        agent_name=agent.name,
                        model_id=agent.model_id,
                        passed=False,
                        score=0.0,
                        failure_category="TIMEOUT",
                        failure_description=f"Task exceeded {timeout_per_task}s timeout",
                    )
                except Exception as e:
                    return EvaluationResult(
                        task_id=task.task_id,
                        agent_name=agent.name,
                        model_id=agent.model_id,
                        passed=False,
                        score=0.0,
                        failure_category="EXECUTION_ERROR",
                        failure_description=str(e),
                    )

        # Run all evaluations
        tasks_pending = [_run_single(t) for t in tasks]
        results = await asyncio.gather(*tasks_pending, return_exceptions=True)

        # Filter out exceptions
        clean_results: list[EvaluationResult] = []
        for r in results:
            if isinstance(r, Exception):
                print(f"Evaluation error: {r}")
                continue
            clean_results.append(r)

        self.results.extend(clean_results)
        return clean_results

    async def _evaluate_single(
        self, agent: CodingAgent, task: BenchmarkTask
    ) -> EvaluationResult:
        """Execute and evaluate a single task."""
        # Run the agent
        output = await agent.execute(task)
        output.end_time = datetime.utcnow()

        # Store the output for later analysis
        await self._store_agent_output(output)

        # Evaluate
        evaluator = self._evaluators.get(task.task_type.name)
        if evaluator:
            result = await evaluator.evaluate(task, output)
        else:
            # Default: basic pass/fall based on human verification if available
            result = EvaluationResult(
                task_id=task.task_id,
                agent_name=agent.name,
                model_id=agent.model_id,
                passed=output.human_correct or False,
                score=1.0 if output.human_correct else 0.0,
            )

        return result

    async def _store_agent_output(self, output: AgentOutput) -> None:
        """Persist agent output to disk for later analysis."""
        out_dir = Path("data/outputs") / output.agent_name
        out_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{output.task_id.stable_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = out_dir / filename

        with open(filepath, "w") as f:
            json.dump(output.to_dict(), f, indent=2, default=str)

    def get_summary_stats(self) -> dict[str, Any]:
        """Generate summary statistics across all results."""
        if not self.results:
            return {}

        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        scores = [r.score for r in self.results]

        # By task type
        by_type: dict[str, dict[str, Any]] = {}
        for r in self.results:
            task = self.tasks.get(str(r.task_id))
            if task:
                tname = task.task_type.name
                if tname not in by_type:
                    by_type[tname] = {"total": 0, "passed": 0, "scores": []}
                by_type[tname]["total"] += 1
                if r.passed:
                    by_type[tname]["passed"] += 1
                by_type[tname]["scores"].append(r.score)

        for tname in by_type:
            scores_list = by_type[tname]["scores"]
            by_type[tname]["pass_rate"] = by_type[tname]["passed"] / max(by_type[tname]["total"], 1)
            by_type[tname]["avg_score"] = float(np.mean(scores_list)) if scores_list else 0.0

        return {
            "total_tasks": total,
            "passed": passed,
            "pass_rate": passed / total,
            "avg_score": float(np.mean(scores)),
            "median_score": float(np.median(scores)),
            "std_score": float(np.std(scores)),
            "by_task_type": by_type,
        }

    def save(self, path: str) -> None:
        """Save the benchmark suite to disk."""
        data = {
            "name": self.name,
            "description": self.description,
            "tasks": {k: v.to_dict() for k, v in self.tasks.items()},
            "results": [r.to_dict() for r in self.results],
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    @classmethod
    def load(cls, path: str) -> BenchmarkSuite:
        """Load a benchmark suite from disk."""
        with open(path) as f:
            data = json.load(f)

        suite = cls(name=data["name"], description=data.get("description", ""))
        for task_data in data.get("tasks", {}).values():
            suite.add_task(BenchmarkTask.from_dict(task_data))

        # Results are reconstructed as dicts for now
        suite.results = data.get("results", [])
        return suite


# -----------------------------------------------------------------------------
# Evaluator Interface
# -----------------------------------------------------------------------------

class Evaluator(ABC):
    """Abstract base for task evaluators."""

    @abstractmethod
    async def evaluate(self, task: BenchmarkTask, output: AgentOutput) -> EvaluationResult:
        """Evaluate agent output against task criteria."""
        pass


class ProgrammaticEvaluator(Evaluator):
    """
    Evaluator that runs test code against agent output.

    Inspired by SWE-bench's test harness approach.
    """

    async def evaluate(self, task: BenchmarkTask, output: AgentOutput) -> EvaluationResult:
        test_results: list[TestResult] = []
        total_weight = 0.0
        earned_weight = 0.0

        for test in task.test_cases:
            if test.test_type == "unit":
                result = await self._run_unit_test(test, output)
            elif test.test_type == "integration":
                result = await self._run_integration_test(test, output)
            elif test.test_type == "behavioral":
                result = await self._run_behavioral_test(test, output)
            else:
                result = TestResult(test_name=test.name, passed=False, score=0.0, details="Unknown test type")

            test_results.append(result)
            total_weight += test.weight
            if result.passed:
                earned_weight += test.weight
            elif test.partial_credit and result.score > 0:
                earned_weight += test.weight * result.score

        final_score = earned_weight / max(total_weight, 1e-6)
        passed = final_score >= 0.99  # Require near-perfect for pass

        return EvaluationResult(
            task_id=task.task_id,
            agent_name=output.agent_name,
            model_id=output.model_id,
            passed=passed,
            score=final_score,
            test_results=test_results,
        )

    async def _run_unit_test(self, test: TestCase, output: AgentOutput) -> TestResult:
        """Execute a unit test against agent output."""
        # Placeholder - actual implementation would run the code
        return TestResult(
            test_name=test.name,
            passed=False,
            score=0.0,
            details="Unit test execution not yet implemented",
        )

    async def _run_integration_test(self, test: TestCase, output: AgentOutput) -> TestResult:
        """Execute an integration test."""
        return TestResult(
            test_name=test.name,
            passed=False,
            score=0.0,
            details="Integration test execution not yet implemented",
        )

    async def _run_behavioral_test(self, test: TestCase, output: AgentOutput) -> TestResult:
        """Evaluate behavioral criteria."""
        return TestResult(
            test_name=test.name,
            passed=False,
            score=0.0,
            details="Behavioral test execution not yet implemented",
        )


class LLMJudgeEvaluator(Evaluator):
    """
    Evaluator that uses an LLM-as-judge to score agent output.

    Inspired by Composo.ai's criteria-less judging approach.
    """

    def __init__(self, judge_model: str = "gpt-4o"):
        self.judge_model = judge_model

    async def evaluate(self, task: BenchmarkTask, output: AgentOutput) -> EvaluationResult:
        # Build evaluation prompt
        eval_prompt = self._build_judge_prompt(task, output)

        # Call judge LLM (placeholder)
        # In production, this would call the actual LLM API
        judge_score = 0.0
        judge_reasoning = "LLM judge not yet implemented"

        # Parse score from judge response
        passed = judge_score >= 0.5

        return EvaluationResult(
            task_id=task.task_id,
            agent_name=output.agent_name,
            model_id=output.model_id,
            passed=passed,
            score=judge_score,
            test_results=[
                TestResult(
                    test_name="llm_judge",
                    passed=passed,
                    score=judge_score,
                    details=judge_reasoning,
                )
            ],
        )

    def _build_judge_prompt(self, task: BenchmarkTask, output: AgentOutput) -> str:
        """Build the prompt for the LLM judge."""
        criteria = "\n".join(f"- {c}" for c in task.success_criteria)
        return f"""You are evaluating a coding agent's output.

Task: {task.instruction}

Success Criteria:
{criteria}

Agent Output:
```
{output.final_code or 'No code produced'}
```

Tool Calls Made: {len(output.tool_calls)}
Steps Taken: {len(output.execution_trajectory)}

Rate the agent's performance from 0 to 10 and explain your reasoning.
Be specific about what succeeded and what failed.
"""


# -----------------------------------------------------------------------------
# Factory Functions for Common Benchmark Patterns
# -----------------------------------------------------------------------------

def create_swe_bench_style_task(
    repo: str,
    issue_id: str,
    issue_description: str,
    base_commit: str,
    test_patch: str,
    gold_patch: str,
) -> BenchmarkTask:
    """
    Create a task in the style of SWE-bench.

    SWE-bench tasks are real GitHub issues with associated test patches.
    """
    return BenchmarkTask(
        task_id=TaskId(namespace=f"swe-bench-{repo}", name=issue_id),
        task_type=TaskType.BUG_FIXING,
        difficulty=Difficulty.MEDIUM,
        instruction=issue_description,
        context_files={
            "test_patch.py": test_patch,
            "expected_fix.patch": gold_patch,
        },
        setup_commands=[
            f"git checkout {base_commit}",
            f"git apply test_patch.py",
        ],
        test_cases=[
            TestCase(
                name="fail_to_pass",
                test_type="integration",
                expected_behavior="Patch resolves the issue and all tests pass",
                weight=1.0,
            ),
        ],
        tags=["swe-bench", repo, "github-issue"],
        language="python",
    )


def create_terminal_bench_style_task(
    task_name: str,
    description: str,
    setup_script: str,
    verification_commands: list[str],
    expected_outputs: list[str],
    difficulty: Difficulty = Difficulty.HARD,
) -> BenchmarkTask:
    """
    Create a task in the style of Terminal-Bench.

    Terminal-Bench tasks test multi-step terminal workflows.
    """
    return BenchmarkTask(
        task_id=TaskId(namespace="terminal-bench", name=task_name),
        task_type=TaskType.TERMINAL_WORKFLOW,
        difficulty=difficulty,
        instruction=description,
        setup_commands=[setup_script],
        test_cases=[
            TestCase(
                name=f"verify_step_{i}",
                test_type="integration",
                expected_behavior=f"Command '{cmd}' produces expected output",
                weight=1.0 / len(verification_commands),
            )
            for i, cmd in enumerate(verification_commands)
        ],
        tags=["terminal-bench", "cli", "workflow"],
        language="bash",
    )


def create_humaneval_style_task(
    task_id: str,
    prompt: str,
    canonical_solution: str,
    test_code: str,
    entry_point: str,
) -> BenchmarkTask:
    """
    Create a task in the style of HumanEval.

    HumanEval tasks test single-function generation from docstrings.
    """
    return BenchmarkTask(
        task_id=TaskId(namespace="humaneval", name=task_id),
        task_type=TaskType.CODE_GENERATION,
        difficulty=Difficulty.EASY,
        instruction=f"Complete the following function:\n\n{prompt}",
        context_files={
            "test.py": test_code,
            "solution.py": canonical_solution,
        },
        test_cases=[
            TestCase(
                name="functional_correctness",
                test_type="unit",
                expected_behavior=f"Function '{entry_point}' passes all test cases",
                weight=1.0,
            ),
        ],
        tags=["humaneval", "function-generation", "python"],
        language="python",
    )
