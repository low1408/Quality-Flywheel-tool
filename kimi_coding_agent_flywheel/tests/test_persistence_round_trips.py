from __future__ import annotations

from datetime import datetime

from kimi_coding_agent_flywheel.core.benchmark_models import (
    BenchmarkTask,
    Difficulty,
    EvaluationResult,
    TaskId,
    TaskType,
    TestResult as BenchmarkTestResult,
)
from kimi_coding_agent_flywheel.core.benchmark_suite import BenchmarkSuite
from kimi_coding_agent_flywheel.core.telemetry_models import EventType, Trace, TraceEvent
from kimi_coding_agent_flywheel.optimization.genetic import GeneticPromptOptimizer
from kimi_coding_agent_flywheel.optimization.models import (
    OptimizationState,
    PromptCandidate,
)
from kimi_coding_agent_flywheel.regression.models import (
    RegressionReport,
    RegressionResult,
    RegressionTest,
)
from kimi_coding_agent_flywheel.regression.suite import RegressionSuite


class _UnusedEvaluator:
    async def evaluate(self, candidate: PromptCandidate) -> dict[str, object]:
        raise AssertionError(f"unexpected evaluation of {candidate.prompt_id}")


def test_benchmark_suite_round_trip_preserves_typed_results(tmp_path) -> None:
    task_id = TaskId(namespace="test", name="round-trip")
    suite = BenchmarkSuite(name="round-trip")
    suite.add_task(
        BenchmarkTask(
            task_id=task_id,
            task_type=TaskType.CODE_GENERATION,
            difficulty=Difficulty.EASY,
            instruction="Return a value.",
        )
    )
    suite.results.append(
        EvaluationResult(
            task_id=task_id,
            agent_name="test-agent",
            passed=True,
            score=0.75,
            test_results=[BenchmarkTestResult("unit", True, 0.75)],
        )
    )

    suite_path = tmp_path / "benchmark.json"
    suite.save(str(suite_path))
    restored = BenchmarkSuite.load(str(suite_path))

    assert isinstance(next(iter(restored.tasks.values())), BenchmarkTask)
    assert isinstance(restored.results[0], EvaluationResult)
    assert isinstance(restored.results[0].test_results[0], BenchmarkTestResult)
    assert restored.get_summary_stats()["avg_score"] == 0.75


def test_trace_round_trip_preserves_spans_counters_and_step_sequence(tmp_path) -> None:
    trace = Trace(trace_id="trace-one", agent_name="test-agent")
    span_id = trace.start_span("generation", span_id="span-one")
    first_event = TraceEvent(
        event_id="event-one",
        event_type=EventType.LLM_REQUEST,
        timestamp=datetime.utcnow(),
        step_number=-1,
        span_id=span_id,
        tokens_in=2,
        tokens_out=3,
    )
    trace.add_event(first_event)
    trace.finalize()

    restored = Trace.load(str(trace.save(str(tmp_path))))

    assert restored.total_llm_calls == 1
    assert restored.total_tokens == 5
    assert restored.spans[span_id].events[0] is restored.events[0]

    next_event = TraceEvent(
        event_id="event-two",
        event_type=EventType.TOOL_CALL,
        timestamp=datetime.utcnow(),
        step_number=-1,
    )
    restored.add_event(next_event)
    assert next_event.step_number == 1
    assert restored.total_tool_calls == 1


def test_optimizer_state_round_trip_preserves_restartable_population(tmp_path) -> None:
    candidate = PromptCandidate(
        prompt_id="candidate-one",
        system_prompt="Be precise.",
        fitness_score=0.8,
        evaluation_results=[{"pass_rate": 0.9}],
        pass_rate=0.9,
    )
    optimizer = GeneticPromptOptimizer(_UnusedEvaluator())
    optimizer.state = OptimizationState(
        generation=3,
        population=[candidate],
        best_candidate=candidate,
        best_fitness_history=[0.5, 0.8],
        avg_fitness_history=[0.4, 0.7],
    )

    state_path = tmp_path / "optimizer.json"
    optimizer.save_state(str(state_path))
    restored = GeneticPromptOptimizer(_UnusedEvaluator())
    restored.load_state(str(state_path))

    assert restored.state.generation == 3
    assert isinstance(restored.state.population[0], PromptCandidate)
    assert restored.state.population[0].evaluation_results == [{"pass_rate": 0.9}]
    assert restored.state.best_candidate is not None
    assert restored.state.best_candidate.prompt_id == "candidate-one"


def test_regression_storage_round_trip_preserves_typed_models(tmp_path) -> None:
    suite = RegressionSuite(name="round-trip", storage_path=str(tmp_path))
    suite.add_test(
        RegressionTest(
            test_id="regression-one",
            name="Regression one",
            description="Protect known behavior.",
            task={"instruction": "Return a value."},
        )
    )
    suite_path = tmp_path / "suite.json"
    suite.save(str(suite_path))
    restored_suite = RegressionSuite.load(str(suite_path))
    assert isinstance(restored_suite.tests["regression-one"], RegressionTest)

    report = RegressionReport(
        run_id="run-one",
        total_tests=1,
        passed_tests=1,
        all_passed=True,
        test_results=[
            RegressionResult(
                test_id="regression-one",
                test_name="Regression one",
                run_id="run-one",
                passed=True,
            )
        ],
    )
    suite._save_report(report)
    restored_report = suite._load_report("run-one")

    assert restored_report is not None
    assert isinstance(restored_report.test_results[0], RegressionResult)
    assert restored_report.test_results[0].passed is True
