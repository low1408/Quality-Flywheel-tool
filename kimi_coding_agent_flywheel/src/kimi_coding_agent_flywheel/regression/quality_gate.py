"""Quality-gate policy for regression reports."""

from __future__ import annotations

from typing import Any

from .models import RegressionReport
from .suite import RegressionSuite

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
