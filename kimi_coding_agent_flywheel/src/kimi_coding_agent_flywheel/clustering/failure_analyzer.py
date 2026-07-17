"""Backward-compatible facade for the failure-analysis subsystem.

The implementation is split by responsibility across ``taxonomy``, ``models``,
``diagnosis``, ``engine``, ``root_cause``, and ``pipeline``. Existing imports
from this module remain supported.
"""

from .diagnosis import LLMJudgeDiagnoser
from .engine import (
    HUMAN_CAUSE_LABELS,
    FailureClusteringEngine,
    normalize_error_message,
    normalize_root_cause,
)
from .models import (
    DiagnosedFailures,
    DiagnosisError,
    DiagnosisInfrastructureError,
    DiagnosisInvalidResponse,
    DiagnosisResult,
    EmbeddedFailure,
    FailureCluster,
    FailureInstance,
    utc_now,
)
from .pipeline import FailureAnalysisPipeline
from .root_cause import RootCauseAnalyzer
from .taxonomy import (
    ANALYSIS_STATE_SCHEMA_VERSION,
    CATEGORY_GROUPS,
    FAILURE_DESCRIPTIONS,
    VALID_SEVERITIES,
    FailureCategory,
)

__all__ = [
    "ANALYSIS_STATE_SCHEMA_VERSION",
    "CATEGORY_GROUPS",
    "FAILURE_DESCRIPTIONS",
    "HUMAN_CAUSE_LABELS",
    "VALID_SEVERITIES",
    "DiagnosedFailures",
    "DiagnosisError",
    "DiagnosisInfrastructureError",
    "DiagnosisInvalidResponse",
    "DiagnosisResult",
    "EmbeddedFailure",
    "FailureAnalysisPipeline",
    "FailureCategory",
    "FailureCluster",
    "FailureClusteringEngine",
    "FailureInstance",
    "LLMJudgeDiagnoser",
    "RootCauseAnalyzer",
    "normalize_error_message",
    "normalize_root_cause",
    "utc_now",
]
