"""
Main Quality Flywheel Orchestrator.

Ties together all components into a continuous improvement loop:
1. Execute agents on benchmark suite
2. Collect and store outputs with telemetry
3. Diagnose failures via LLM-as-judge
4. Cluster failures by root cause
5. Optimize prompts based on cluster analysis
6. Verify fixes against regression suite
7. Monitor production for new failure modes
8. Feed new failures back into the loop
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from core.benchmark import BenchmarkSuite, CodingAgent, EvaluationResult
from core.telemetry import TraceAnalyzer
from clustering.failure_analyzer import (
    FailureAnalysisPipeline,
    FailureCluster,
    FailureInstance,
    LLMJudgeDiagnoser,
    FailureClusteringEngine,
    RootCauseAnalyzer,
)
from optimization.prompt_optimizer import (
    BenchmarkPromptEvaluator,
    CompositePromptOptimizer,
    ErrorDrivenOptimizer,
    GeneticPromptOptimizer,
    PromptCandidate,
)
from regression.regression_suite import QualityGate, RegressionReport, RegressionSuite
from monitoring.dashboard import ConsoleDashboard, ProductionMonitor, UserFeedbackCollector


@dataclass
class FlywheelConfig:
    """Configuration for the quality flywheel."""
    # Benchmark settings
    benchmark_suite_path: str = "data/benchmarks/default_suite.json"
    max_concurrent_evals: int = 4
    eval_timeout: int = 300

    # Failure analysis
    enable_llm_judge: bool = True
    min_cluster_size: int = 2
    embedding_fn: Any | None = None  # Function for generating embeddings

    # Prompt optimization
    genetic_generations: int = 5
    genetic_population: int = 10
    enable_error_driven_opt: bool = True

    # Regression
    regression_suite_path: str = "data/regression/suite.json"
    min_pass_rate: float = 0.90
    max_pass_rate_degradation: float = 0.05

    # Monitoring
    enable_production_monitoring: bool = True
    drift_check_interval_minutes: int = 60

    # Storage
    output_dir: str = "data/flywheel"
    trace_dir: str = "data/traces"

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_suite_path": self.benchmark_suite_path,
            "max_concurrent_evals": self.max_concurrent_evals,
            "eval_timeout": self.eval_timeout,
            "enable_llm_judge": self.enable_llm_judge,
            "min_cluster_size": self.min_cluster_size,
            "genetic_generations": self.genetic_generations,
            "genetic_population": self.genetic_population,
            "enable_error_driven_opt": self.enable_error_driven_opt,
            "regression_suite_path": self.regression_suite_path,
            "min_pass_rate": self.min_pass_rate,
            "max_pass_rate_degradation": self.max_pass_rate_degradation,
            "enable_production_monitoring": self.enable_production_monitoring,
            "drift_check_interval_minutes": self.drift_check_interval_minutes,
            "output_dir": self.output_dir,
            "trace_dir": self.trace_dir,
        }


@dataclass
class FlywheelState:
    """Current state of the quality flywheel."""
    iteration: int = 0
    timestamp: datetime = field(default_factory=datetime.utcnow)

    # Current best configuration
    current_prompt: str = ""
    current_agent_config: dict[str, Any] = field(default_factory=dict)

    # Performance tracking
    current_pass_rate: float = 0.0
    current_avg_score: float = 0.0
    current_cost_per_task: float = 0.0

    # History
    pass_rate_history: list[float] = field(default_factory=list)
    score_history: list[float] = field(default_factory=list)
    cost_history: list[float] = field(default_factory=list)

    # Failure tracking
    known_failure_clusters: list[dict[str, Any]] = field(default_factory=list)
    total_failures_diagnosed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "timestamp": self.timestamp.isoformat(),
            "current_pass_rate": self.current_pass_rate,
            "current_avg_score": self.current_avg_score,
            "current_cost_per_task": self.current_cost_per_task,
            "pass_rate_history": self.pass_rate_history,
            "score_history": self.score_history,
            "cost_history": self.cost_history,
            "known_failure_clusters": self.known_failure_clusters,
            "total_failures_diagnosed": self.total_failures_diagnosed,
        }


class QualityFlywheel:
    """
    Main orchestrator for the continuous quality improvement flywheel.

    Usage:
        flywheel = QualityFlywheel(config)
        await flywheel.initialize()
        await flywheel.run_iteration(agent)
    """

    def __init__(self, config: FlywheelConfig | None = None):
        self.config = config or FlywheelConfig()
        self.state = FlywheelState()

        # Components (initialized in initialize())
        self.benchmark_suite: BenchmarkSuite | None = None
        self.failure_pipeline: FailureAnalysisPipeline | None = None
        self.prompt_optimizer: CompositePromptOptimizer | None = None
        self.regression_suite: RegressionSuite | None = None
        self.quality_gate: QualityGate | None = None
        self.production_monitor: ProductionMonitor | None = None
        self.feedback_collector: UserFeedbackCollector | None = None

        # Storage
        self.output_dir = Path(self.config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """Initialize all flywheel components."""
        print("=" * 60)
        print("INITIALIZING QUALITY FLYWHEEL")
        print("=" * 60)

        # 1. Load or create benchmark suite
        suite_path = Path(self.config.benchmark_suite_path)
        if suite_path.exists():
            self.benchmark_suite = BenchmarkSuite.load(str(suite_path))
            print(f"Loaded benchmark suite: {self.benchmark_suite.name}")
            print(f"  Tasks: {len(self.benchmark_suite.tasks)}")
        else:
            self.benchmark_suite = BenchmarkSuite(name="default", description="Default benchmark suite")
            print("Created new benchmark suite")

        # 2. Initialize failure analysis pipeline
        diagnoser = LLMJudgeDiagnoser() if self.config.enable_llm_judge else LLMJudgeDiagnoser(judge_fn=None)
        clusterer = FailureClusteringEngine(
            embedding_fn=self.config.embedding_fn,
            min_cluster_size=self.config.min_cluster_size,
        )
        self.failure_pipeline = FailureAnalysisPipeline(
            diagnoser=diagnoser,
            clusterer=clusterer,
        )
        print("Initialized failure analysis pipeline")

        # 3. Initialize prompt optimizer (will be set up per-agent)
        print("Prompt optimizer ready (will be configured per-agent)")

        # 4. Load or create regression suite
        reg_path = Path(self.config.regression_suite_path)
        if reg_path.exists():
            self.regression_suite = RegressionSuite.load(str(reg_path))
            print(f"Loaded regression suite: {len(self.regression_suite.tests)} tests")
        else:
            self.regression_suite = RegressionSuite(name="default")
            print("Created new regression suite")

        # 5. Initialize quality gate
        self.quality_gate = QualityGate(
            regression_suite=self.regression_suite,
            absolute_thresholds={"min_pass_rate": self.config.min_pass_rate},
            relative_thresholds={"max_pass_rate_degradation": self.config.max_pass_rate_degradation},
        )
        print("Initialized quality gate")

        # 6. Initialize production monitoring
        if self.config.enable_production_monitoring:
            self.production_monitor = ProductionMonitor()
            self.feedback_collector = UserFeedbackCollector()
            print("Initialized production monitoring")

        # Load previous state if available
        state_path = self.output_dir / "flywheel_state.json"
        if state_path.exists():
            with open(state_path) as f:
                state_data = json.load(f)
                self.state.current_pass_rate = state_data.get("current_pass_rate", 0.0)
                self.state.current_avg_score = state_data.get("current_avg_score", 0.0)
                self.state.pass_rate_history = state_data.get("pass_rate_history", [])
                self.state.score_history = state_data.get("score_history", [])
                self.state.iteration = state_data.get("iteration", 0)
            print(f"Loaded previous state (iteration {self.state.iteration})")

        print("=" * 60)
        print("FLYWHEEL READY")
        print("=" * 60)

    async def run_iteration(self, agent: CodingAgent) -> dict[str, Any]:
        """
        Execute one full iteration of the quality flywheel.

        This is the main entry point - each call represents one complete
        cycle of: evaluate -> diagnose -> optimize -> verify.
        """
        self.state.iteration += 1
        iteration = self.state.iteration

        print(f"\n{'#' * 60}")
        print(f"# FLYWHEEL ITERATION {iteration}")
        print(f"# Agent: {agent.name} ({agent.model_id or 'unknown'})")
        print(f"# Time: {datetime.utcnow().isoformat()}")
        print(f"{'#' * 60}")

        results = {
            "iteration": iteration,
            "timestamp": datetime.utcnow().isoformat(),
            "agent": {"name": agent.name, "model": agent.model_id},
        }

        # ------------------------------------------------------------------
        # PHASE 1: EVALUATE - Run benchmark suite
        # ------------------------------------------------------------------
        print(f"\n{'=' * 50}")
        print("PHASE 1: BENCHMARK EVALUATION")
        print(f"{'=' * 50}")

        eval_results = await self.benchmark_suite.run_evaluation(
            agent=agent,
            max_concurrent=self.config.max_concurrent_evals,
            timeout_per_task=self.config.eval_timeout,
        )

        stats = self.benchmark_suite.get_summary_stats()
        results["evaluation"] = {
            "total_tasks": stats.get("total_tasks", 0),
            "pass_rate": stats.get("pass_rate", 0.0),
            "avg_score": stats.get("avg_score", 0.0),
            "by_task_type": stats.get("by_task_type", {}),
        }

        self.state.current_pass_rate = stats.get("pass_rate", 0.0)
        self.state.current_avg_score = stats.get("avg_score", 0.0)
        self.state.pass_rate_history.append(self.state.current_pass_rate)
        self.state.score_history.append(self.state.current_avg_score)

        print(f"  Tasks evaluated: {stats.get('total_tasks', 0)}")
        print(f"  Pass rate: {stats.get('pass_rate', 0):.3f}")
        print(f"  Avg score: {stats.get('avg_score', 0):.3f}")

        # ------------------------------------------------------------------
        # PHASE 2: DIAGNOSE - Analyze failures
        # ------------------------------------------------------------------
        print(f"\n{'=' * 50}")
        print("PHASE 2: FAILURE DIAGNOSIS & CLUSTERING")
        print(f"{'=' * 50}")

        # Load traces from evaluation
        trace_dir = Path(self.config.trace_dir)
        traces = []
        if trace_dir.exists():
            for trace_file in trace_dir.glob(f"*{agent.name}*/*.json"):
                try:
                    with open(trace_file) as f:
                        traces.append(json.load(f))
                except Exception:
                    pass

        # Process traces through failure pipeline
        if traces:
            failures = self.failure_pipeline.process_traces(traces)
            clusters = self.failure_pipeline.run_clustering()

            self.state.total_failures_diagnosed += len(failures)
            self.state.known_failure_clusters = [c.to_dict() for c in clusters]

            results["diagnosis"] = {
                "failures_found": len(failures),
                "clusters_identified": len(clusters),
                "top_clusters": [c.to_dict() for c in clusters[:5]],
            }

            print(f"  Failures diagnosed: {len(failures)}")
            print(f"  Clusters identified: {len(clusters)}")
            for c in clusters[:5]:
                print(f"    - {c.label}: {len(c.failures)} failures")
        else:
            print("  No traces found for analysis")
            results["diagnosis"] = {"failures_found": 0, "clusters_identified": 0}

        # ------------------------------------------------------------------
        # PHASE 3: OPTIMIZE - Improve prompts based on failures
        # ------------------------------------------------------------------
        print(f"\n{'=' * 50}")
        print("PHASE 3: PROMPT OPTIMIZATION")
        print(f"{'=' * 50}")

        current_prompt = agent.get_system_prompt()
        clusters = self.failure_pipeline.clusterer.clusters if self.failure_pipeline else []

        if clusters and len(clusters) > 0:
            # Set up evaluator
            evaluator = BenchmarkPromptEvaluator(
                benchmark_suite=self.benchmark_suite,
                agent_factory=lambda prompt: agent.__class__(
                    model=agent.model_id or "default",
                    system_prompt=prompt,
                ),
                num_tasks=min(10, len(self.benchmark_suite.tasks)),
            )

            # Set up optimizer
            genetic_opt = GeneticPromptOptimizer(
                evaluator=evaluator,
                num_generations=self.config.genetic_generations,
                population_size=self.config.genetic_population,
            )

            error_driven_opt = ErrorDrivenOptimizer(evaluator=evaluator) if self.config.enable_error_driven_opt else None

            optimizer = CompositePromptOptimizer(
                genetic_optimizer=genetic_opt,
                error_driven_optimizer=error_driven_opt,
            )

            # Run optimization
            try:
                best_candidate = await optimizer.optimize(
                    seed_prompt=current_prompt,
                    failure_clusters=clusters,
                )

                results["optimization"] = {
                    "original_prompt_length": len(current_prompt),
                    "optimized_prompt_length": len(best_candidate.system_prompt),
                    "fitness_improvement": best_candidate.fitness_score,
                    "pass_rate": best_candidate.pass_rate,
                }

                print(f"  Original prompt length: {len(current_prompt)}")
                print(f"  Optimized prompt length: {len(best_candidate.system_prompt)}")
                print(f"  Fitness score: {best_candidate.fitness_score:.3f}")

                # Update agent prompt
                agent.update_system_prompt(best_candidate.system_prompt)
                self.state.current_prompt = best_candidate.system_prompt

            except Exception as e:
                print(f"  Optimization failed: {e}")
                results["optimization"] = {"error": str(e)}
        else:
            print("  No failure clusters to optimize against")
            results["optimization"] = {"status": "skipped_no_failures"}

        # ------------------------------------------------------------------
        # PHASE 4: VERIFY - Regression testing
        # ------------------------------------------------------------------
        print(f"\n{'=' * 50}")
        print("PHASE 4: REGRESSION VERIFICATION")
        print(f"{'=' * 50}")

        # Add new regression tests from identified failures
        if self.failure_pipeline:
            for cluster in self.failure_pipeline.clusterer.clusters:
                for failure in cluster.failures[:1]:  # Add one per cluster
                    task_data = {"task_id": failure.task_id, "instruction": failure.description}
                    self.regression_suite.add_test_from_failure(
                        failure_id=failure.failure_id,
                        task=task_data,
                        cluster_id=cluster.cluster_id,
                    )

        # Run regression suite
        reg_report = await self.regression_suite.run_regression(
            agent=agent,
            run_id=f"flywheel_iter_{iteration}",
        )

        # Quality gate check
        gate_result = self.quality_gate.generate_gate_report(reg_report)

        results["regression"] = {
            "total_tests": reg_report.total_tests,
            "passed": reg_report.passed_tests,
            "failed": reg_report.failed_tests,
            "new_regressions": len(reg_report.new_regressions),
            "gate_passed": gate_result["gate_passed"],
            "recommendation": gate_result["recommendation"],
        }

        print(f"  Regression tests: {reg_report.passed_tests}/{reg_report.total_tests} passed")
        print(f"  New regressions: {len(reg_report.new_regressions)}")
        print(f"  Quality gate: {'PASS' if gate_result['gate_passed'] else 'FAIL'}")

        # ------------------------------------------------------------------
        # PHASE 5: UPDATE STATE
        # ------------------------------------------------------------------
        self._save_state()
        self.benchmark_suite.save(str(self.output_dir / f"benchmark_iter_{iteration}.json"))
        self.regression_suite.save()

        print(f"\n{'=' * 50}")
        print("ITERATION COMPLETE")
        print(f"{'=' * 50}")

        return results

    async def monitor_production(self, traces: list[Any]) -> dict[str, Any]:
        """
        Process production traces for monitoring and drift detection.

        This feeds production data back into the flywheel to detect
        new failure modes that weren't caught by benchmarks.
        """
        if not self.production_monitor:
            return {"error": "Production monitoring not enabled"}

        from monitoring.dashboard import ProductionTrace

        # Convert to production traces
        production_traces = []
        for t in traces:
            if isinstance(t, dict):
                pt = ProductionTrace(
                    trace_id=t.get("trace_id", ""),
                    timestamp=datetime.fromisoformat(t["timestamp"]) if "timestamp" in t else datetime.utcnow(),
                    agent_name=t.get("agent_name", "unknown"),
                    model_id=t.get("model_id"),
                    task_type=t.get("task_type", ""),
                    success=t.get("success", False),
                    score=t.get("score", 0.0),
                    failure_subcategory=t.get("failure_subcategory"),
                    user_feedback=t.get("user_feedback"),
                    user_correction=t.get("user_correction"),
                )
                production_traces.append(pt)

        # Ingest
        self.production_monitor.ingest_batch(production_traces)

        # Check for drift
        alerts = self.production_monitor.check_drift()

        # Generate dashboard data
        dashboard_data = self.production_monitor.generate_dashboard_data()

        return {
            "traces_ingested": len(production_traces),
            "alerts_generated": len(alerts),
            "alerts": [a.to_dict() for a in alerts],
            "dashboard": dashboard_data,
        }

    def record_user_feedback(
        self,
        trace_id: str,
        was_correct: bool,
        user_notes: str = "",
        user_correction: str | None = None,
    ) -> None:
        """Record user feedback for a trace."""
        if self.feedback_collector:
            self.feedback_collector.record_feedback(
                trace_id=trace_id,
                was_correct=was_correct,
                user_notes=user_notes,
                user_correction=user_correction,
            )

    def get_status(self) -> dict[str, Any]:
        """Get current flywheel status."""
        return {
            "iteration": self.state.iteration,
            "current_pass_rate": self.state.current_pass_rate,
            "current_avg_score": self.state.current_avg_score,
            "pass_rate_history": self.state.pass_rate_history,
            "score_history": self.state.score_history,
            "known_failure_clusters": len(self.state.known_failure_clusters),
            "total_failures_diagnosed": self.state.total_failures_diagnosed,
            "regression_tests": len(self.regression_suite.tests) if self.regression_suite else 0,
        }

    def _save_state(self) -> None:
        """Save flywheel state to disk."""
        state_path = self.output_dir / "flywheel_state.json"
        with open(state_path, "w") as f:
            json.dump(self.state.to_dict(), f, indent=2, default=str)

    def generate_report(self) -> dict[str, Any]:
        """Generate a comprehensive report of flywheel activity."""
        return {
            "flywheel_status": self.get_status(),
            "config": self.config.to_dict(),
            "benchmark_summary": self.benchmark_suite.get_summary_stats() if self.benchmark_suite else {},
            "regression_tests": len(self.regression_suite.tests) if self.regression_suite else 0,
            "production_alerts": len(self.production_monitor.alerts) if self.production_monitor else 0,
        }
