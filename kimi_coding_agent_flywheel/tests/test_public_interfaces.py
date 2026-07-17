from __future__ import annotations

import asyncio
import importlib
import sys

from kimi_coding_agent_flywheel.cli import build_parser
from kimi_coding_agent_flywheel.clustering import failure_analyzer
from kimi_coding_agent_flywheel.clustering.diagnosis import LLMJudgeDiagnoser
from kimi_coding_agent_flywheel.clustering.engine import FailureClusteringEngine
from kimi_coding_agent_flywheel.clustering.models import FailureInstance
from kimi_coding_agent_flywheel.core import aq_adapter, benchmark, telemetry
from kimi_coding_agent_flywheel.core.aq_analysis_store import AQAnalysisStoreMixin
from kimi_coding_agent_flywheel.core.aq_ingestion import AQIngestionMixin
from kimi_coding_agent_flywheel.core.aq_projection import AQTraceProjectionMixin
from kimi_coding_agent_flywheel.core.benchmark_models import (
    BenchmarkTask,
    Difficulty,
    TaskId,
    TaskType,
)
from kimi_coding_agent_flywheel.core.benchmark_suite import BenchmarkSuite
from kimi_coding_agent_flywheel.core.telemetry_models import EventType, Trace
from kimi_coding_agent_flywheel.core.tracing import Tracer
from kimi_coding_agent_flywheel.examples.example_agent_wrappers import MockCodingAgent
from kimi_coding_agent_flywheel.optimization import prompt_optimizer
from kimi_coding_agent_flywheel.optimization.genetic import GeneticPromptOptimizer
from kimi_coding_agent_flywheel.optimization.models import PromptCandidate
from kimi_coding_agent_flywheel.regression import regression_suite
from kimi_coding_agent_flywheel.regression.quality_gate import QualityGate
from kimi_coding_agent_flywheel.regression.suite import RegressionSuite


def test_legacy_facades_reexport_focused_implementations() -> None:
    assert failure_analyzer.FailureInstance is FailureInstance
    assert failure_analyzer.LLMJudgeDiagnoser is LLMJudgeDiagnoser
    assert failure_analyzer.FailureClusteringEngine is FailureClusteringEngine

    assert benchmark.BenchmarkTask is BenchmarkTask
    assert benchmark.BenchmarkSuite is BenchmarkSuite
    assert telemetry.Trace is Trace
    assert telemetry.Tracer is Tracer

    assert prompt_optimizer.PromptCandidate is PromptCandidate
    assert prompt_optimizer.GeneticPromptOptimizer is GeneticPromptOptimizer
    assert regression_suite.RegressionSuite is RegressionSuite
    assert regression_suite.QualityGate is QualityGate


def test_aq_adapter_facade_composes_focused_capabilities() -> None:
    adapter_type = aq_adapter.AQDbAdapter
    assert issubclass(adapter_type, AQIngestionMixin)
    assert issubclass(adapter_type, AQTraceProjectionMixin)
    assert issubclass(adapter_type, AQAnalysisStoreMixin)
    assert callable(adapter_type.save_events)
    assert callable(adapter_type.load_run_traces)
    assert callable(adapter_type.persist_analysis_results)


def test_facade_all_exports_resolve() -> None:
    facades = [failure_analyzer, benchmark, telemetry, prompt_optimizer, regression_suite]
    for facade in facades:
        assert facade.__all__
        assert all(hasattr(facade, name) for name in facade.__all__)


def test_documented_cli_parser_exposes_analyze() -> None:
    args = build_parser().parse_args(
        [
            "analyze",
            "--db",
            "quality.sqlite3",
            "--run-id",
            "run_one",
            "--judge-command-json",
            '["python", "judge.py"]',
        ]
    )
    assert args.command == "analyze"
    assert args.run_ids == ["run_one"]


def test_example_modules_import_without_mutating_python_path() -> None:
    original_path = list(sys.path)

    importlib.import_module(
        "kimi_coding_agent_flywheel.examples.example_agent_wrappers"
    )
    importlib.import_module("kimi_coding_agent_flywheel.examples.run_flywheel_demo")

    assert sys.path == original_path


def test_mock_agent_records_events_without_masking_failure(monkeypatch) -> None:
    agent = MockCodingAgent(simulate_success_rate=0.0)
    captured_traces = []
    monkeypatch.setattr(agent.tracer, "_save_trace", captured_traces.append)
    task = BenchmarkTask(
        task_id=TaskId(namespace="test", name="mock-agent"),
        task_type=TaskType.CODE_GENERATION,
        difficulty=Difficulty.EASY,
        instruction="Return a value.",
    )

    output = asyncio.run(agent.execute(task))

    assert output.final_code is not None
    assert len(captured_traces) == 1
    event_types = [event.event_type for event in captured_traces[0].events]
    assert event_types.count(EventType.THOUGHT) == 1
    assert event_types.count(EventType.TOOL_CALL) == 3
    assert event_types.count(EventType.ERROR) == 1
    assert event_types.count(EventType.METRIC) == 1
