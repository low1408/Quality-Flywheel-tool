"""Backward-compatible facade for benchmark APIs."""

from ..eval.evaluators import Evaluator, LLMJudgeEvaluator, ProgrammaticEvaluator
from .agents import CodingAgent
from .benchmark_factories import (
    create_humaneval_style_task,
    create_swe_bench_style_task,
    create_terminal_bench_style_task,
)
from .benchmark_models import (
    AgentOutput,
    BenchmarkTask,
    Difficulty,
    EvaluationResult,
    TaskId,
    TaskType,
    TestCase,
    TestResult,
    ToolCall,
    TrajectoryStep,
)
from .benchmark_suite import BenchmarkSuite

__all__ = [
    "AgentOutput",
    "BenchmarkSuite",
    "BenchmarkTask",
    "CodingAgent",
    "Difficulty",
    "EvaluationResult",
    "Evaluator",
    "LLMJudgeEvaluator",
    "ProgrammaticEvaluator",
    "TaskId",
    "TaskType",
    "TestCase",
    "TestResult",
    "ToolCall",
    "TrajectoryStep",
    "create_humaneval_style_task",
    "create_swe_bench_style_task",
    "create_terminal_bench_style_task",
]
