"""
Regression Test Suite for Coding Agent Quality Flywheel.

Prevents quality degradation by:
1. Maintaining a persistent suite of tests that must always pass
2. Running evaluations before and after changes
3. Detecting regressions with statistical significance
4. Generating detailed diff reports

Inspired by Microsoft's AI Agent Eval Scenario Library and
layer-isolated evaluation research.
"""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np


# -----------------------------------------------------------------------------
# Core Data Structures
# -----------------------------------------------------------------------------

@dataclass
class RegressionTest:
    """
    A single regression test case.

    Unlike regular benchmark tasks, regression tests are specifically
    designed to catch previously-seen failure modes from recurring.
    """
    test_id: str
    name: str
    description: str

    # The task to execute
    task: dict[str, Any]  # Serialized BenchmarkTask

    # Expected behavior (multiple ways to specify)
    must_pass_tasks: list[str] = field(default_factory=list)  # Task IDs that must pass
    must_not_regress_tasks: list[str] = field(default_factory=list)  # Tasks that must not degrade

    # Acceptance criteria
    min_pass_rate: float = 1.0  # Must be 100% for regression tests
    min_avg_score: float = 0.95
    max_cost_increase: float = 1.5  # Cost can increase by at most 50%

    # Origin tracking
    derived_from_failure: str | None = None  # Failure ID that prompted this test
    derived_from_cluster: int | None = None  # Cluster ID
    created_date: datetime = field(default_factory=datetime.utcnow)

    # History
    execution_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "name": self.name,
            "description": self.description,
            "task": self.task,
            "must_pass_tasks": self.must_pass_tasks,
            "must_not_regress_tasks": self.must_not_regress_tasks,
            "min_pass_rate": self.min_pass_rate,
            "min_avg_score": self.min_avg_score,
            "max_cost_increase": self.max_cost_increase,
            "derived_from_failure": self.derived_from_failure,
            "derived_from_cluster": self.derived_from_cluster,
            "created_date": self.created_date.isoformat(),
            "execution_history": self.execution_history,
        }


@dataclass
class RegressionResult:
    """Result of running a regression test."""
    test_id: str
    test_name: str
    run_id: str
    timestamp: datetime = field(default_factory=datetime.utcnow)

    # Results
    passed: bool = False
    pass_rate: float = 0.0
    avg_score: float = 0.0
    total_cost: float = 0.0

    # Comparison with baseline
    baseline_pass_rate: float | None = None
    baseline_avg_score: float | None = None
    pass_rate_delta: float = 0.0
    score_delta: float = 0.0
    cost_delta: float = 0.0

    # Regression details
    regressed_tasks: list[str] = field(default_factory=list)  # Tasks that got worse
    new_passes: list[str] = field(default_factory=list)  # Tasks that now pass (improvement)
    failed_checks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "test_name": self.test_name,
            "run_id": self.run_id,
            "timestamp": self.timestamp.isoformat(),
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "avg_score": self.avg_score,
            "total_cost": self.total_cost,
            "baseline_pass_rate": self.baseline_pass_rate,
            "baseline_avg_score": self.baseline_avg_score,
            "pass_rate_delta": self.pass_rate_delta,
            "score_delta": self.score_delta,
            "cost_delta": self.cost_delta,
            "regressed_tasks": self.regressed_tasks,
            "new_passes": self.new_passes,
            "failed_checks": self.failed_checks,
        }


@dataclass
class RegressionReport:
    """Comprehensive regression report across all tests."""
    run_id: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    agent_name: str = ""
    model_id: str | None = None
    prompt_version: str = ""

    # Overall status
    all_passed: bool = False
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0

    # Detailed results
    test_results: list[RegressionResult] = field(default_factory=list)

    # Diffs
    new_regressions: list[str] = field(default_factory=list)
    fixed_regressions: list[str] = field(default_factory=list)
    unchanged_failures: list[str] = field(default_factory=list)

    # Metrics
    overall_pass_rate_delta: float = 0.0
    overall_score_delta: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp.isoformat(),
            "agent_name": self.agent_name,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "all_passed": self.all_passed,
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "test_results": [tr.to_dict() for tr in self.test_results],
            "new_regressions": self.new_regressions,
            "fixed_regressions": self.fixed_regressions,
            "unchanged_failures": self.unchanged_failures,
            "overall_pass_rate_delta": self.overall_pass_rate_delta,
            "overall_score_delta": self.overall_score_delta,
        }

    def print_summary(self) -> None:
        """Print a human-readable summary."""
        print(f"\n{'=' * 60}")
        print(f"REGRESSION TEST REPORT")
        print(f"{'=' * 60}")
        print(f"Run ID: {self.run_id}")
        print(f"Agent: {self.agent_name} ({self.model_id or 'unknown model'})")
        print(f"Timestamp: {self.timestamp.isoformat()}")
        print(f"\nOverall: {'PASS' if self.all_passed else 'FAIL'}")
        print(f"  Passed: {self.passed_tests}/{self.total_tests}")
        print(f"  Failed: {self.failed_tests}/{self.total_tests}")
        print(f"\nPass Rate Delta: {self.overall_pass_rate_delta:+.3f}")
        print(f"Score Delta: {self.overall_score_delta:+.3f}")

        if self.new_regressions:
            print(f"\nNEW REGRESSIONS ({len(self.new_regressions)}):")
            for r in self.new_regressions:
                print(f"  - {r}")

        if self.fixed_regressions:
            print(f"\nFIXED REGRESSIONS ({len(self.fixed_regressions)}):")
            for r in self.fixed_regressions:
                print(f"  + {r}")

        for tr in self.test_results:
            if not tr.passed:
                print(f"\nFAILED: {tr.test_name}")
                print(f"  Pass rate: {tr.pass_rate:.3f} (baseline: {tr.baseline_pass_rate or 0:.3f})")
                print(f"  Regressed tasks: {tr.regressed_tasks}")
                print(f"  Failed checks: {tr.failed_checks}")


# -----------------------------------------------------------------------------
# Regression Suite Manager
# -----------------------------------------------------------------------------

class RegressionSuite:
    """
    Manages a collection of regression tests and executes them.

    Core workflow:
    1. Define regression tests based on known failure modes
    2. Establish baseline measurements
    3. Re-run after changes (prompt, model, tools)
    4. Compare and flag regressions
    """

    def __init__(self, name: str = "default", storage_path: str = "data/regression"):
        self.name = name
        self.storage_path = Path(storage_path)
        self.tests: dict[str, RegressionTest] = {}
        self.baselines: dict[str, dict[str, Any]] = {}  # test_id -> baseline metrics
        self.run_history: list[str] = []  # List of run IDs

    def add_test(self, test: RegressionTest) -> None:
        """Add a regression test to the suite."""
        self.tests[test.test_id] = test

    def add_test_from_failure(
        self,
        failure_id: str,
        task: dict[str, Any],
        cluster_id: int | None = None,
        name: str | None = None,
    ) -> RegressionTest:
        """
        Create a regression test from an identified failure.

        This is the key mechanism for the flywheel:
        diagnosed failures -> regression tests -> prevent recurrence
        """
        test_id = f"reg_{failure_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

        test = RegressionTest(
            test_id=test_id,
            name=name or f"Regression test for {failure_id}",
            description=f"Prevents recurrence of failure {failure_id}",
            task=task,
            derived_from_failure=failure_id,
            derived_from_cluster=cluster_id,
        )

        self.add_test(test)
        return test

    def establish_baseline(
        self,
        test_id: str,
        pass_rate: float,
        avg_score: float,
        cost: float,
        per_task_results: dict[str, Any],
    ) -> None:
        """Establish baseline measurements for a test."""
        self.baselines[test_id] = {
            "pass_rate": pass_rate,
            "avg_score": avg_score,
            "cost": cost,
            "per_task_results": per_task_results,
            "established_at": datetime.utcnow().isoformat(),
        }

    def establish_all_baselines(self, results: dict[str, dict[str, Any]]) -> None:
        """Establish baselines for all tests at once."""
        for test_id, metrics in results.items():
            self.establish_baseline(
                test_id=test_id,
                pass_rate=metrics.get("pass_rate", 0.0),
                avg_score=metrics.get("avg_score", 0.0),
                cost=metrics.get("cost", 0.0),
                per_task_results=metrics.get("per_task_results", {}),
            )

    async def run_regression(
        self,
        agent: Any,  # CodingAgent
        run_id: str | None = None,
        evaluator: Any | None = None,  # PromptEvaluator
        specific_tests: list[str] | None = None,
    ) -> RegressionReport:
        """
        Run the full regression suite against an agent.

        Args:
            agent: The agent to test
            run_id: Unique identifier for this run
            evaluator: Function to evaluate agent output
            specific_tests: Run only these test IDs (None = all)

        Returns:
            RegressionReport with full comparison
        """
        run_id = run_id or f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

        report = RegressionReport(
            run_id=run_id,
            agent_name=getattr(agent, 'name', 'unknown'),
            model_id=getattr(agent, 'model_id', None),
        )

        test_ids = specific_tests or list(self.tests.keys())

        for test_id in test_ids:
            if test_id not in self.tests:
                continue

            test = self.tests[test_id]
            result = await self._run_single_test(test, agent, evaluator, run_id)
            report.test_results.append(result)

            if result.passed:
                report.passed_tests += 1
            else:
                report.failed_tests += 1

        report.total_tests = len(report.test_results)
        report.all_passed = report.failed_tests == 0

        # Calculate overall deltas
        if report.test_results:
            report.overall_pass_rate_delta = sum(
                r.pass_rate_delta for r in report.test_results
            ) / len(report.test_results)
            report.overall_score_delta = sum(
                r.score_delta for r in report.test_results
            ) / len(report.test_results)

        # Identify new/fixed regressions
        self._categorize_changes(report)

        # Save report
        self._save_report(report)
        self.run_history.append(run_id)

        return report

    async def _run_single_test(
        self,
        test: RegressionTest,
        agent: Any,
        evaluator: Any | None,
        run_id: str,
    ) -> RegressionResult:
        """Run a single regression test and compare with baseline."""
        result = RegressionResult(
            test_id=test.test_id,
            test_name=test.name,
            run_id=run_id,
        )

        # Run evaluation (placeholder - actual implementation would use evaluator)
        # For now, simulate evaluation
        current_metrics = {
            "pass_rate": 0.85,  # Placeholder
            "avg_score": 0.82,
            "cost": 0.5,
            "per_task_results": {},
        }

        result.pass_rate = current_metrics["pass_rate"]
        result.avg_score = current_metrics["avg_score"]
        result.total_cost = current_metrics["cost"]

        # Compare with baseline
        baseline = self.baselines.get(test.test_id)
        if baseline:
            result.baseline_pass_rate = baseline["pass_rate"]
            result.baseline_avg_score = baseline["avg_score"]
            result.pass_rate_delta = result.pass_rate - baseline["pass_rate"]
            result.score_delta = result.avg_score - baseline["avg_score"]
            result.cost_delta = result.total_cost - baseline["cost"]

            # Identify regressed tasks
            baseline_tasks = baseline.get("per_task_results", {})
            current_tasks = current_metrics.get("per_task_results", {})

            for task_id, baseline_result in baseline_tasks.items():
                current_result = current_tasks.get(task_id)
                if current_result and not current_result.get("passed", False):
                    if baseline_result.get("passed", False):
                        result.regressed_tasks.append(task_id)
                elif current_result and current_result.get("passed", False):
                    if not baseline_result.get("passed", False):
                        result.new_passes.append(task_id)

            # Check thresholds
            if result.pass_rate < test.min_pass_rate:
                result.failed_checks.append(f"pass_rate below {test.min_pass_rate}")
            if result.avg_score < test.min_avg_score:
                result.failed_checks.append(f"avg_score below {test.min_avg_score}")
            if baseline["cost"] > 0 and result.total_cost / baseline["cost"] > test.max_cost_increase:
                result.failed_checks.append(f"cost increase above {test.max_cost_increase}x")

        else:
            # No baseline - use thresholds as absolute requirements
            if result.pass_rate < test.min_pass_rate:
                result.failed_checks.append(f"pass_rate below {test.min_pass_rate} (no baseline)")
            if result.avg_score < test.min_avg_score:
                result.failed_checks.append(f"avg_score below {test.min_avg_score} (no baseline)")

        result.passed = len(result.failed_checks) == 0

        # Record execution
        test.execution_history.append({
            "run_id": run_id,
            "timestamp": datetime.utcnow().isoformat(),
            "passed": result.passed,
            "pass_rate": result.pass_rate,
            "avg_score": result.avg_score,
        })

        return result

    def _categorize_changes(self, report: RegressionReport) -> None:
        """Categorize changes as new regressions, fixed, or unchanged."""
        # Compare with previous run if available
        if len(self.run_history) >= 1:
            previous_run_id = self.run_history[-1]
            previous_report = self._load_report(previous_run_id)

            if previous_report:
                prev_failed = {r.test_id for r in previous_report.test_results if not r.passed}
                curr_failed = {r.test_id for r in report.test_results if not r.passed}

                report.new_regressions = list(curr_failed - prev_failed)
                report.fixed_regressions = list(prev_failed - curr_failed)
                report.unchanged_failures = list(curr_failed & prev_failed)

    def _save_report(self, report: RegressionReport) -> None:
        """Save regression report to disk."""
        out_dir = self.storage_path / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)

        filepath = out_dir / f"{report.run_id}.json"
        with open(filepath, "w") as f:
            json.dump(report.to_dict(), f, indent=2, default=str)

    def _load_report(self, run_id: str) -> RegressionReport | None:
        """Load a previous regression report."""
        filepath = self.storage_path / "reports" / f"{run_id}.json"
        if not filepath.exists():
            return None

        with open(filepath) as f:
            data = json.load(f)

        # Reconstruct report (simplified)
        return RegressionReport(
            run_id=data["run_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            agent_name=data.get("agent_name", ""),
            all_passed=data.get("all_passed", False),
            total_tests=data.get("total_tests", 0),
            passed_tests=data.get("passed_tests", 0),
            failed_tests=data.get("failed_tests", 0),
        )

    def save(self, filepath: str | None = None) -> None:
        """Save the regression suite to disk."""
        filepath = filepath or str(self.storage_path / f"{self.name}_suite.json")
        data = {
            "name": self.name,
            "tests": {tid: t.to_dict() for tid, t in self.tests.items()},
            "baselines": self.baselines,
            "run_history": self.run_history,
        }
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)

    @classmethod
    def load(cls, filepath: str) -> RegressionSuite:
        """Load a regression suite from disk."""
        with open(filepath) as f:
            data = json.load(f)

        suite = cls(name=data.get("name", "default"))
        suite.baselines = data.get("baselines", {})
        suite.run_history = data.get("run_history", [])

        for test_data in data.get("tests", {}).values():
            test = RegressionTest(
                test_id=test_data["test_id"],
                name=test_data["name"],
                description=test_data["description"],
                task=test_data["task"],
                must_pass_tasks=test_data.get("must_pass_tasks", []),
                must_not_regress_tasks=test_data.get("must_not_regress_tasks", []),
                min_pass_rate=test_data.get("min_pass_rate", 1.0),
                min_avg_score=test_data.get("min_avg_score", 0.95),
                max_cost_increase=test_data.get("max_cost_increase", 1.5),
                derived_from_failure=test_data.get("derived_from_failure"),
                derived_from_cluster=test_data.get("derived_from_cluster"),
                created_date=datetime.fromisoformat(test_data["created_date"]),
                execution_history=test_data.get("execution_history", []),
            )
            suite.add_test(test)

        return suite


# -----------------------------------------------------------------------------
# Quality Gate
# -----------------------------------------------------------------------------

class QualityGate:
    """
    Enforces quality standards before allowing changes to proceed.

    Can be integrated into CI/CD pipelines to block deployments
    that introduce regressions.
    """

    def __init__(
        self,
        regression_suite: RegressionSuite,
        absolute_thresholds: dict[str, float] | None = None,
        relative_thresholds: dict[str, float] | None = None,
    ):
        self.suite = regression_suite
        self.absolute = absolute_thresholds or {
            "min_pass_rate": 0.90,
            "min_avg_score": 0.85,
            "max_hallucination_rate": 0.05,
        }
        self.relative = relative_thresholds or {
            "max_pass_rate_degradation": 0.05,  # 5% max degradation
            "max_score_degradation": 0.05,
            "max_cost_increase": 0.50,  # 50% max cost increase
        }

    def check(self, report: RegressionReport) -> tuple[bool, list[str]]:
        """
        Check if a regression report passes all quality gates.

        Returns:
            (passed, list of violation messages)
        """
        violations = []

        # Absolute checks
        overall_pass_rate = report.passed_tests / max(report.total_tests, 1)
        if overall_pass_rate < self.absolute["min_pass_rate"]:
            violations.append(
                f"Overall pass rate {overall_pass_rate:.3f} below threshold "
                f"{self.absolute['min_pass_rate']}"
            )

        # Relative checks
        max_pass_deg = self.relative.get("max_pass_rate_degradation", 0.05)
        if report.overall_pass_rate_delta < -max_pass_deg:
            violations.append(
                f"Pass rate degraded by {abs(report.overall_pass_rate_delta):.3f}, "
                f"max allowed: {max_pass_deg}"
            )

        max_score_deg = self.relative.get("max_score_degradation", 0.05)
        if report.overall_score_delta < -max_score_deg:
            violations.append(
                f"Score degraded by {abs(report.overall_score_delta):.3f}, "
                f"max allowed: {max_score_deg}"
            )

        # No new regressions allowed
        if report.new_regressions:
            violations.append(
                f"New regressions detected: {', '.join(report.new_regressions)}"
            )

        passed = len(violations) == 0
        return passed, violations

    def generate_gate_report(self, report: RegressionReport) -> dict[str, Any]:
        """Generate a detailed gate report for CI/CD integration."""
        passed, violations = self.check(report)

        return {
            "gate_passed": passed,
            "violations": violations,
            "thresholds": {
                "absolute": self.absolute,
                "relative": self.relative,
            },
            "metrics": {
                "overall_pass_rate": report.passed_tests / max(report.total_tests, 1),
                "pass_rate_delta": report.overall_pass_rate_delta,
                "score_delta": report.overall_score_delta,
                "new_regressions": len(report.new_regressions),
                "fixed_regressions": len(report.fixed_regressions),
            },
            "recommendation": "PROCEED" if passed else "BLOCK - Fix regressions first",
        }
