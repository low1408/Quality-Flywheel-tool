"""Root-cause summaries and regression-test recommendations for clusters."""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from .models import FailureCluster
from .taxonomy import FailureCategory

# -----------------------------------------------------------------------------
# Root Cause Analysis Engine
# -----------------------------------------------------------------------------

class RootCauseAnalyzer:
    """
    Deep root cause analysis for identified failure clusters.

    Goes beyond surface-level classification to identify:
    - Which prompt components are responsible
    - Whether the issue is model-specific or agent-agnostic
    - The minimal fix needed
    """

    def __init__(self, llm_fn: Callable[[str], str] | None = None):
        self.llm_fn = llm_fn

    def analyze_cluster(self, cluster: FailureCluster) -> dict[str, Any]:
        """Perform deep RCA on a failure cluster."""
        analysis = {
            "cluster_id": cluster.cluster_id,
            "cluster_label": cluster.label,
            "failure_count": len(cluster.failures),
        }

        # 1. Prompt component analysis
        analysis["prompt_component_analysis"] = self._analyze_prompt_components(cluster)

        # 2. Model vs agent analysis
        analysis["model_agent_breakdown"] = self._analyze_model_agent_distribution(cluster)

        # 3. Temporal pattern
        analysis["temporal_pattern"] = self._analyze_temporal_pattern(cluster)

        # 4. Minimal fix recommendation
        analysis["minimal_fix"] = self._recommend_minimal_fix(cluster)

        # 5. Regression test specification
        analysis["regression_tests"] = self._specify_regression_tests(cluster)

        return analysis

    def _analyze_prompt_components(self, cluster: FailureCluster) -> dict[str, Any]:
        """Analyze which prompt components are most associated with failures."""
        component_counts = Counter()
        for f in cluster.failures:
            if f.affected_prompt_component:
                component_counts[f.affected_prompt_component] += 1

        total = len(cluster.failures)
        return {
            "component_distribution": {
                comp: {"count": count, "percentage": count / total * 100}
                for comp, count in component_counts.most_common()
            },
            "primary_component": component_counts.most_common(1)[0][0] if component_counts else "unknown",
        }

    def _analyze_model_agent_distribution(self, cluster: FailureCluster) -> dict[str, Any]:
        """Analyze whether failures are model-specific or agent-agnostic."""
        agent_counts = Counter(f.agent_name for f in cluster.failures)
        model_counts = Counter(f.model_id for f in cluster.failures if f.model_id)

        total = len(cluster.failures)
        agent_concentration = max(agent_counts.values()) / total if agent_counts else 0
        model_concentration = max(model_counts.values()) / total if model_counts else 0

        return {
            "affected_agents": dict(agent_counts),
            "affected_models": dict(model_counts),
            "agent_concentration": agent_concentration,
            "model_concentration": model_concentration,
            "is_agent_specific": agent_concentration > 0.7,
            "is_model_specific": model_concentration > 0.7,
            "is_systemic": agent_concentration < 0.5 and model_concentration < 0.5,
        }

    def _analyze_temporal_pattern(self, cluster: FailureCluster) -> dict[str, Any]:
        """Analyze if failures are clustered in time (suggesting a specific change caused them)."""
        timestamps = [f.timestamp for f in cluster.failures]
        if len(timestamps) < 2:
            return {"pattern": "insufficient_data"}

        timestamps.sort()
        gaps = [(timestamps[i+1] - timestamps[i]).total_seconds() / 3600
                for i in range(len(timestamps) - 1)]

        avg_gap = sum(gaps) / len(gaps) if gaps else 0
        max_gap = max(gaps) if gaps else 0

        # If failures are clustered (large gap after initial cluster), suggests a change caused them
        is_burst = max_gap > avg_gap * 3 if avg_gap > 0 else False

        return {
            "first_occurrence": timestamps[0].isoformat(),
            "last_occurrence": timestamps[-1].isoformat(),
            "avg_gap_hours": avg_gap,
            "max_gap_hours": max_gap,
            "is_burst_pattern": is_burst,
            "pattern": "burst" if is_burst else "continuous",
        }

    def _recommend_minimal_fix(self, cluster: FailureCluster) -> dict[str, Any]:
        """Recommend the smallest change that would address this cluster."""
        if cluster.dominant_subcategory in [
            FailureCategory.SUBCATEGORY_WRONG_TOOL,
            FailureCategory.SUBCATEGORY_WRONG_ARGS,
        ]:
            return {
                "fix_type": "tool_improvement",
                "description": "Improve tool definitions and add validation",
                "estimated_effort": "small",
                "confidence": "high",
            }
        elif cluster.dominant_subcategory in [
            FailureCategory.SUBCATEGORY_DISOBEY_SPEC,
            FailureCategory.SUBCATEGORY_NO_TERMINATION,
        ]:
            return {
                "fix_type": "prompt_enhancement",
                "description": "Add explicit constraints and completion criteria to system prompt",
                "estimated_effort": "small",
                "confidence": "high",
            }
        elif cluster.dominant_subcategory in [
            FailureCategory.CODE_SYNTAX,
            FailureCategory.CODE_LOGIC,
        ]:
            return {
                "fix_type": "workflow_improvement",
                "description": "Add code validation and test execution steps",
                "estimated_effort": "medium",
                "confidence": "medium",
            }
        else:
            return {
                "fix_type": "investigation_needed",
                "description": "Requires deeper investigation to determine minimal fix",
                "estimated_effort": "large",
                "confidence": "low",
            }

    def _specify_regression_tests(self, cluster: FailureCluster) -> list[dict[str, Any]]:
        """Generate regression test specifications for this cluster."""
        tests = []

        # Create a regression test based on the failure pattern
        for i, failure in enumerate(cluster.failures[:3]):  # Top 3 representative failures
            tests.append({
                "test_id": f"regression_{cluster.cluster_id}_{i}",
                "description": f"Verify fix for: {failure.description[:100]}",
                "trigger_condition": failure.probable_cause,
                "verification_method": "Execute task and verify no failure occurs",
                "priority": "high" if failure.severity in ["high", "critical"] else "medium",
            })

        return tests
