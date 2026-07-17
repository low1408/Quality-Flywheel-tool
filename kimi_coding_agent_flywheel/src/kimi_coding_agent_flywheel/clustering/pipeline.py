"""Application service that composes diagnosis, clustering, and RCA."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from .diagnosis import LLMJudgeDiagnoser
from .engine import FailureClusteringEngine
from .models import (
    DiagnosedFailures,
    DiagnosisError,
    DiagnosisInfrastructureError,
    DiagnosisInvalidResponse,
    FailureCluster,
    FailureInstance,
    utc_now,
)
from .root_cause import RootCauseAnalyzer
from .taxonomy import ANALYSIS_STATE_SCHEMA_VERSION

# -----------------------------------------------------------------------------
# Main Failure Analysis Pipeline
# -----------------------------------------------------------------------------

class FailureAnalysisPipeline:
    """
    End-to-end pipeline for analyzing agent failures.

    Orchestrates:
    1. LLM-based diagnosis of individual traces
    2. Embedding-based clustering of similar failures
    3. Root cause analysis of clusters
    4. Comparison with previous runs
    5. Actionable fix recommendations
    """

    def __init__(
        self,
        diagnoser: LLMJudgeDiagnoser | None = None,
        clusterer: FailureClusteringEngine | None = None,
        rca_engine: RootCauseAnalyzer | None = None,
    ):
        if diagnoser is None:
            raise ValueError(
                "FailureAnalysisPipeline requires an explicit diagnoser. "
                "Use LLMJudgeDiagnoser(judge_fn=...) for runtime analysis or "
                "LLMJudgeDiagnoser(use_mock_judge=True) for tests/demos."
            )
        self.diagnoser = diagnoser
        self.clusterer = clusterer or FailureClusteringEngine()
        self.rca_engine = rca_engine or RootCauseAnalyzer()

        self.all_failures: list[FailureInstance] = []
        self.diagnosis_errors: list[DiagnosisError] = []
        self.previous_clusters: list[FailureCluster] | None = None
        self._clustering_has_run = False

    def process_traces(self, traces: list[dict[str, Any]]) -> list[FailureInstance]:
        """
        Process a batch of execution traces and extract failures.

        Args:
            traces: List of trace dictionaries from the telemetry system

        Returns:
            List of diagnosed FailureInstance objects
        """
        all_failures = []

        for trace in traces:
            result = self.diagnoser.diagnose(
                trace,
                task_description=str(trace.get("task_description") or trace.get("task_id") or ""),
            )
            if isinstance(result, DiagnosedFailures):
                all_failures.extend(result.failures)
            else:
                self.diagnosis_errors.append(result)

        self.all_failures.extend(all_failures)
        return all_failures

    def run_clustering(self) -> list[FailureCluster]:
        """Cluster all diagnosed failures."""
        clusters = self.clusterer.cluster(self.all_failures)
        self._clustering_has_run = True
        return clusters

    def generate_report(self) -> dict[str, Any]:
        """Generate comprehensive failure analysis report."""
        if self.all_failures and not self._clustering_has_run:
            raise RuntimeError("run_clustering() must be called before generate_report().")

        clusters = self.clusterer.clusters

        # Run RCA on each cluster
        cluster_analyses = []
        for cluster in clusters:
            analysis = self.rca_engine.analyze_cluster(cluster)
            cluster_analyses.append(analysis)

        # Overall statistics
        category_distribution = Counter(f.category for f in self.all_failures if f.category)
        severity_distribution = Counter(f.severity for f in self.all_failures)

        # Compare with previous if available
        comparison = None
        if self.previous_clusters is not None:
            comparison = self.clusterer.compare_with_previous(self.previous_clusters)

        return {
            "summary": {
                "total_failures_diagnosed": len(self.all_failures),
                "diagnosis_errors": len(self.diagnosis_errors),
                "clusters_identified": len(clusters),
                "category_distribution": dict(category_distribution),
                "severity_distribution": dict(severity_distribution),
            },
            "clusters": [c.to_dict() for c in clusters],
            "cluster_analyses": cluster_analyses,
            "drift_comparison": comparison,
            "top_recommendations": self._generate_top_recommendations(clusters),
        }

    def _generate_top_recommendations(self, clusters: list[FailureCluster]) -> list[dict[str, Any]]:
        """Generate prioritized list of fix recommendations."""
        recommendations = []

        for cluster in sorted(clusters, key=lambda c: len(c.failures), reverse=True)[:5]:
            recommendations.append({
                "priority": len(cluster.failures),
                "cluster_label": cluster.label,
                "failure_count": len(cluster.failures),
                "affected_agents": list(cluster.affected_agents),
                "suggested_fix": cluster.suggested_prompt_fix or cluster.suggested_tool_fix,
                "fix_target": "prompt" if cluster.suggested_prompt_fix else "tool",
                "estimated_effort": "small" if len(cluster.failures) < 5 else "medium",
            })

        return recommendations

    def save_state(self, filepath: str) -> None:
        """Save the current analysis state to disk."""
        state = {
            "schema_version": ANALYSIS_STATE_SCHEMA_VERSION,
            "failures": [f.to_dict() for f in self.all_failures],
            "clusters": [self._cluster_to_state_dict(c) for c in self.clusterer.clusters],
            "diagnosis_errors": [error.to_dict() for error in self.diagnosis_errors],
            "timestamp": utc_now().isoformat(),
        }
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.tmp")
        with open(temp_path, "w") as f:
            json.dump(state, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        temp_path.replace(path)

    def load_state(self, filepath: str) -> None:
        """Load a previous analysis state."""
        with open(filepath) as f:
            state = json.load(f)

        if state.get("schema_version") not in (None, ANALYSIS_STATE_SCHEMA_VERSION):
            raise ValueError(f"Unsupported analysis state schema_version: {state.get('schema_version')}")

        self.all_failures = [
            FailureInstance.from_dict(failure_data)
            for failure_data in state.get("failures", [])
        ]
        failure_lookup = {failure.failure_id: failure for failure in self.all_failures}

        loaded_clusters = [
            self._cluster_from_state_dict(cluster_data, failure_lookup)
            for cluster_data in state.get("clusters", [])
        ]
        self.clusterer.clusters = loaded_clusters
        self.previous_clusters = loaded_clusters
        self._clustering_has_run = True
        self.diagnosis_errors = [
            self._diagnosis_error_from_state_dict(error_data)
            for error_data in state.get("diagnosis_errors", [])
        ]

    def _cluster_to_state_dict(self, cluster: FailureCluster) -> dict[str, Any]:
        state = cluster.to_dict()
        state["failure_ids"] = [failure.failure_id for failure in cluster.failures]
        return state

    def _cluster_from_state_dict(
        self,
        data: dict[str, Any],
        failure_lookup: dict[str, FailureInstance],
    ) -> FailureCluster:
        failure_ids = data.get("failure_ids", [])
        failures = [
            failure_lookup[failure_id]
            for failure_id in failure_ids
            if failure_id in failure_lookup
        ]

        return FailureCluster(
            cluster_id=int(data.get("cluster_id", 0)),
            label=str(data.get("label", "Unknown Failure Pattern")),
            description=str(data.get("description", "")),
            failures=failures,
            dominant_category=data.get("dominant_category"),
            dominant_subcategory=data.get("dominant_subcategory"),
            affected_agents=set(data.get("affected_agents", [])),
            affected_models=set(data.get("affected_models", [])),
            common_keywords=list(data.get("common_keywords", [])),
            common_tool_calls=list(data.get("common_tool_calls", [])),
            avg_severity=str(data.get("avg_severity", "medium")),
            suggested_prompt_fix=str(data.get("suggested_prompt_fix", "")),
            suggested_tool_fix=str(data.get("suggested_tool_fix", "")),
            regression_tests_needed=list(data.get("regression_tests_needed", [])),
        )

    def _diagnosis_error_from_state_dict(self, data: dict[str, Any]) -> DiagnosisError:
        error_type = data.get("type")
        if error_type == "infrastructure_error":
            return DiagnosisInfrastructureError(
                trace_id=data.get("trace_id"),
                task_id=str(data.get("task_id", "unknown")),
                agent_name=str(data.get("agent_name", "unknown")),
                message=str(data.get("message", "")),
                exception_type=str(data.get("exception_type", "Exception")),
            )

        return DiagnosisInvalidResponse(
            trace_id=data.get("trace_id"),
            task_id=str(data.get("task_id", "unknown")),
            agent_name=str(data.get("agent_name", "unknown")),
            message=str(data.get("message", "")),
            response_excerpt=str(data.get("response_excerpt", "")),
        )
