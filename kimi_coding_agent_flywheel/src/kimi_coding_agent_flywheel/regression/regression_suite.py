"""Backward-compatible facade for regression APIs."""

from .models import RegressionReport, RegressionResult, RegressionTest
from .quality_gate import QualityGate
from .suite import RegressionSuite

__all__ = [
    "QualityGate",
    "RegressionReport",
    "RegressionResult",
    "RegressionSuite",
    "RegressionTest",
]
