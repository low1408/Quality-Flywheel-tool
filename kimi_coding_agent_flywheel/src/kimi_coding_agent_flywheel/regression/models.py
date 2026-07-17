"""Regression test, result, and report models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegressionTest:
        """Reconstruct a regression test from suite storage."""
        created_date = data.get("created_date")
        return cls(
            test_id=str(data["test_id"]),
            name=str(data["name"]),
            description=str(data["description"]),
            task=dict(data.get("task", {})),
            must_pass_tasks=list(data.get("must_pass_tasks", [])),
            must_not_regress_tasks=list(data.get("must_not_regress_tasks", [])),
            min_pass_rate=float(data.get("min_pass_rate", 1.0)),
            min_avg_score=float(data.get("min_avg_score", 0.95)),
            max_cost_increase=float(data.get("max_cost_increase", 1.5)),
            derived_from_failure=data.get("derived_from_failure"),
            derived_from_cluster=data.get("derived_from_cluster"),
            created_date=(
                datetime.fromisoformat(created_date)
                if isinstance(created_date, str)
                else datetime.utcnow()
            ),
            execution_history=list(data.get("execution_history", [])),
        )


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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegressionResult:
        """Reconstruct a typed per-test regression result."""
        timestamp = data.get("timestamp")
        return cls(
            test_id=str(data["test_id"]),
            test_name=str(data["test_name"]),
            run_id=str(data["run_id"]),
            timestamp=(
                datetime.fromisoformat(timestamp)
                if isinstance(timestamp, str)
                else datetime.utcnow()
            ),
            passed=bool(data.get("passed", False)),
            pass_rate=float(data.get("pass_rate", 0.0)),
            avg_score=float(data.get("avg_score", 0.0)),
            total_cost=float(data.get("total_cost", 0.0)),
            baseline_pass_rate=data.get("baseline_pass_rate"),
            baseline_avg_score=data.get("baseline_avg_score"),
            pass_rate_delta=float(data.get("pass_rate_delta", 0.0)),
            score_delta=float(data.get("score_delta", 0.0)),
            cost_delta=float(data.get("cost_delta", 0.0)),
            regressed_tasks=list(data.get("regressed_tasks", [])),
            new_passes=list(data.get("new_passes", [])),
            failed_checks=list(data.get("failed_checks", [])),
        )


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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegressionReport:
        """Reconstruct a complete report for historical comparisons."""
        timestamp = data.get("timestamp")
        return cls(
            run_id=str(data["run_id"]),
            timestamp=(
                datetime.fromisoformat(timestamp)
                if isinstance(timestamp, str)
                else datetime.utcnow()
            ),
            agent_name=str(data.get("agent_name", "")),
            model_id=data.get("model_id"),
            prompt_version=str(data.get("prompt_version", "")),
            all_passed=bool(data.get("all_passed", False)),
            total_tests=int(data.get("total_tests", 0)),
            passed_tests=int(data.get("passed_tests", 0)),
            failed_tests=int(data.get("failed_tests", 0)),
            test_results=[
                RegressionResult.from_dict(result)
                for result in data.get("test_results", [])
            ],
            new_regressions=list(data.get("new_regressions", [])),
            fixed_regressions=list(data.get("fixed_regressions", [])),
            unchanged_failures=list(data.get("unchanged_failures", [])),
            overall_pass_rate_delta=float(data.get("overall_pass_rate_delta", 0.0)),
            overall_score_delta=float(data.get("overall_score_delta", 0.0)),
        )

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
