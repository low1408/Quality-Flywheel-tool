"""Regression suite lifecycle, persistence, and execution orchestration."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import RegressionReport, RegressionResult, RegressionTest

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

        return RegressionReport.from_dict(data)

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
            suite.add_test(RegressionTest.from_dict(test_data))

        return suite

    def load_from_aq_cases(self, repo_path: str | Path | None = None) -> None:
        """Load regression tests from the agent-quality cases directory."""
        cases_dir = Path(repo_path or Path.cwd()) / ".agent-quality" / "cases"
        if not cases_dir.exists():
            return
            
        import yaml
        from agent_quality.config import _parse_tiny_yaml
        
        for case_dir in cases_dir.iterdir():
            if not case_dir.is_dir():
                continue
            case_yaml_path = case_dir / "case.yaml"
            prompt_path = case_dir / "prompt.md"
            if not case_yaml_path.exists():
                continue
                
            try:
                # Load case details
                text = case_yaml_path.read_text(encoding="utf-8")
                try:
                    case_data = yaml.safe_load(text)
                except Exception:
                    case_data = _parse_tiny_yaml(text)
                
                # Load prompt
                prompt = ""
                if prompt_path.exists():
                    prompt = prompt_path.read_text(encoding="utf-8")
                
                # Construct Kimi DTO structure
                test_id = case_data.get("id", case_dir.name)
                task_data = {
                    "task_id": f"regression::{test_id}",
                    "instruction": prompt,
                }
                
                test = RegressionTest(
                    test_id=test_id,
                    name=case_data.get("name", f"Regression test {test_id}"),
                    description=case_data.get("description", f"Prevents recurrence of {test_id}"),
                    task=task_data,
                    derived_from_failure=case_data.get("source", {}).get("run_id"),
                    derived_from_cluster=None,
                    created_date=datetime.utcnow(),
                )
                self.add_test(test)
            except Exception as e:
                import sys
                print(f"Warning: Failed to load regression case {case_dir.name}: {e}", file=sys.stderr)

