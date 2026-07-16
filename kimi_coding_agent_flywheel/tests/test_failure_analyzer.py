import json
import tempfile
import unittest
from pathlib import Path

from kimi_coding_agent_flywheel.clustering.failure_analyzer import (
    DiagnosedFailures,
    DiagnosisInfrastructureError,
    DiagnosisInvalidResponse,
    FailureAnalysisPipeline,
    FailureCategory,
    FailureClusteringEngine,
    FailureInstance,
    LLMJudgeDiagnoser,
)


def same_embedding(texts: list[str]) -> list[list[float]]:
    return [[1.0, 0.0] for _ in texts]


def make_failure(failure_id: str, trace_id: str) -> FailureInstance:
    return FailureInstance(
        failure_id=failure_id,
        task_id="task-1",
        agent_name="agent",
        category="Code Quality",
        subcategory=FailureCategory.CODE_SYNTAX,
        description="Generated code contains a syntax error.",
        severity="high",
        trace_id=trace_id,
    )


class StaticDiagnoser:
    def diagnose(self, trace_data: dict, task_description: str = "") -> DiagnosedFailures:
        trace_id = trace_data["trace_id"]
        return DiagnosedFailures([make_failure(f"failure-{trace_id}", trace_id)])


class FailureAnalyzerTests(unittest.TestCase):
    def test_clusterer_clusters_added_failures(self) -> None:
        failures = [
            make_failure("failure-1", "trace-1"),
            make_failure("failure-2", "trace-2"),
        ]
        clusterer = FailureClusteringEngine(embedding_fn=same_embedding, min_cluster_size=2, eps=0.1)

        clusterer.add_failures(failures)
        clusters = clusterer.cluster()

        self.assertEqual(len(clusters), 1)
        self.assertEqual([failure.failure_id for failure in clusters[0].failures], ["failure-1", "failure-2"])

    def test_clusterer_writes_embeddings_to_failures(self) -> None:
        failures = [
            make_failure("failure-1", "trace-1"),
            make_failure("failure-2", "trace-2"),
        ]
        clusterer = FailureClusteringEngine(embedding_fn=same_embedding, min_cluster_size=2, eps=0.1)

        clusterer.cluster(failures)

        self.assertEqual(failures[0].embedding, [1.0, 0.0])
        self.assertEqual(failures[1].embedding, [1.0, 0.0])

    def test_pipeline_clustering_is_idempotent(self) -> None:
        pipeline = FailureAnalysisPipeline(
            diagnoser=StaticDiagnoser(),
            clusterer=FailureClusteringEngine(embedding_fn=same_embedding, min_cluster_size=2, eps=0.1),
        )
        pipeline.process_traces([{"trace_id": "trace-1"}, {"trace_id": "trace-2"}])

        first = pipeline.run_clustering()
        second = pipeline.run_clustering()

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(len(second[0].failures), 2)

    def test_report_requires_clustering_after_failures(self) -> None:
        pipeline = FailureAnalysisPipeline(
            diagnoser=StaticDiagnoser(),
            clusterer=FailureClusteringEngine(embedding_fn=same_embedding, min_cluster_size=2, eps=0.1),
        )
        pipeline.process_traces([{"trace_id": "trace-1"}, {"trace_id": "trace-2"}])

        with self.assertRaises(RuntimeError):
            pipeline.generate_report()

    def test_compare_with_previous_ignores_missing_trace_ids(self) -> None:
        clusterer = FailureClusteringEngine(embedding_fn=same_embedding, min_cluster_size=2, eps=0.1)
        current = FailureInstance(failure_id="current", task_id="task", agent_name="agent")
        previous = FailureInstance(failure_id="previous", task_id="task", agent_name="agent")
        clusterer.clusters = [
            clusterer._build_cluster(1, [current], [0]),
        ]
        previous_clusters = [
            clusterer._build_cluster(2, [previous], [0]),
        ]

        comparison = clusterer.compare_with_previous(previous_clusters)

        self.assertEqual(comparison["matched_clusters"], [])
        self.assertEqual(comparison["new_clusters"], [1])
        self.assertEqual(comparison["resolved_clusters"], [2])

    def test_pipeline_requires_explicit_diagnoser(self) -> None:
        with self.assertRaises(ValueError):
            FailureAnalysisPipeline()

    def test_llm_diagnoser_requires_judge_or_explicit_mock(self) -> None:
        with self.assertRaises(ValueError):
            LLMJudgeDiagnoser()

        diagnoser = LLMJudgeDiagnoser(use_mock_judge=True)
        result = diagnoser.diagnose({
            "trace_id": "trace-1",
            "task_id": "task-1",
            "agent_name": "agent",
            "events": [{"event_type": "ERROR", "content": "Syntax error"}],
        })

        self.assertIsInstance(result, DiagnosedFailures)

    def test_judge_exception_returns_infrastructure_error(self) -> None:
        def failing_judge(prompt: str) -> str:
            raise RuntimeError("rate limit")

        diagnoser = LLMJudgeDiagnoser(judge_fn=failing_judge)
        result = diagnoser.diagnose({"trace_id": "trace-1", "task_id": "task-1", "agent_name": "agent"})

        self.assertIsInstance(result, DiagnosisInfrastructureError)
        self.assertEqual(result.exception_type, "RuntimeError")
        self.assertEqual(result.message, "rate limit")

    def test_invalid_judge_response_is_not_a_failure(self) -> None:
        diagnoser = LLMJudgeDiagnoser(judge_fn=lambda prompt: "not json")
        result = diagnoser.diagnose({"trace_id": "trace-1", "task_id": "task-1", "agent_name": "agent"})

        self.assertIsInstance(result, DiagnosisInvalidResponse)

    def test_save_load_state_restores_failures_and_previous_clusters(self) -> None:
        pipeline = FailureAnalysisPipeline(
            diagnoser=StaticDiagnoser(),
            clusterer=FailureClusteringEngine(embedding_fn=same_embedding, min_cluster_size=2, eps=0.1),
        )
        pipeline.process_traces([{"trace_id": "trace-1"}, {"trace_id": "trace-2"}])
        pipeline.run_clustering()

        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "analysis_state.json"
            pipeline.save_state(str(state_path))

            raw_state = json.loads(state_path.read_text())
            self.assertEqual(raw_state["schema_version"], 1)
            self.assertEqual(raw_state["clusters"][0]["failure_ids"], ["failure-trace-1", "failure-trace-2"])

            restored = FailureAnalysisPipeline(
                diagnoser=StaticDiagnoser(),
                clusterer=FailureClusteringEngine(embedding_fn=same_embedding, min_cluster_size=2, eps=0.1),
            )
            restored.load_state(str(state_path))

        self.assertEqual(len(restored.all_failures), 2)
        self.assertEqual(len(restored.previous_clusters), 1)
        self.assertEqual(len(restored.previous_clusters[0].failures), 2)
        self.assertEqual(len(restored.clusterer.clusters), 1)

    def test_cross_domain_same_root_cause_clusters(self) -> None:
        # Cross-domain same root cause
        f1 = FailureInstance(
            failure_id="fail-1",
            task_id="task-1",
            agent_name="agent",
            category="Specification Issues",
            subcategory="ambiguous_prompt_interpretation",
            description="The frontend CSS layout ignores mobile responsive constraints.",
            error_message="",
            probable_cause="The requirements were underspecified.",
        )
        f2 = FailureInstance(
            failure_id="fail-2",
            task_id="task-1",
            agent_name="agent",
            category="Specification Issues",
            subcategory="ambiguous_prompt_interpretation",
            description="The backend database API endpoint omits pagination parameters.",
            error_message="",
            probable_cause="The prompt was vague.",
        )
        # Even with CSS/API disjoint terms, they should cluster together because they share the same normalized root cause prompt_underspecification
        clusterer = FailureClusteringEngine(min_cluster_size=2, eps=0.5)
        clusters = clusterer.cluster([f1, f2])
        self.assertEqual(len(clusters), 1)
        self.assertIn("Prompt Underspecification", clusters[0].label)

    def test_same_domain_different_root_causes_do_not_cluster(self) -> None:
        # Same domain (CSS) but different root causes
        f1 = FailureInstance(
            failure_id="fail-1",
            task_id="task-1",
            agent_name="agent",
            category="Specification Issues",
            subcategory="ambiguous_prompt_interpretation",
            description="CSS rules for buttons were not specified.",
            probable_cause="The requirements were vague.",
        )
        f2 = FailureInstance(
            failure_id="fail-2",
            task_id="task-1",
            agent_name="agent",
            category="Code Quality",
            subcategory="code_syntax_error",
            description="CSS parsing error in button rules.",
            probable_cause="Syntax error due to typo.",
        )
        # They should NOT cluster together because of different root causes
        clusterer = FailureClusteringEngine(min_cluster_size=2, eps=0.3)
        clusters = clusterer.cluster([f1, f2])
        self.assertEqual(len(clusters), 0)

    def test_task_id_and_framework_terms_do_not_affect_partition(self) -> None:
        # Changing task_id and framework terms should not affect clustering when root causes are identical
        f1 = FailureInstance(
            failure_id="fail-1",
            task_id="task-1",
            agent_name="agent",
            category="Specification Issues",
            subcategory="ambiguous_prompt_interpretation",
            description="React layout ignores constraints.",
            probable_cause="vague prompt",
        )
        f2 = FailureInstance(
            failure_id="fail-2",
            task_id="task-2", # different task
            agent_name="agent",
            category="Specification Issues",
            subcategory="ambiguous_prompt_interpretation",
            description="Django view omits route parameters.",
            probable_cause="vague prompt",
        )
        clusterer = FailureClusteringEngine(min_cluster_size=2, eps=0.5)
        clusters = clusterer.cluster([f1, f2])
        self.assertEqual(len(clusters), 1)

    def test_embedding_failure_is_explicit(self) -> None:
        def raising_embedding(texts: list[str]) -> list[list[float]]:
            raise ValueError("model offline")
            
        clusterer = FailureClusteringEngine(embedding_fn=raising_embedding, min_cluster_size=2, eps=0.3)
        f1 = FailureInstance(failure_id="f1", task_id="t1", agent_name="agent", probable_cause="cause")
        f2 = FailureInstance(failure_id="f2", task_id="t2", agent_name="agent", probable_cause="cause")
        
        with self.assertRaises(RuntimeError) as ctx:
            clusterer.cluster([f1, f2])
        self.assertIn("Clustering feature extraction failed", str(ctx.exception))

    def test_membership_confidence_is_calculated_from_centroid(self) -> None:
        f1 = FailureInstance(
            failure_id="fail-1",
            task_id="task-1",
            agent_name="agent",
            category="Specification Issues",
            subcategory="ambiguous_prompt_interpretation",
            probable_cause="vague prompt",
        )
        f2 = FailureInstance(
            failure_id="fail-2",
            task_id="task-1",
            agent_name="agent",
            category="Specification Issues",
            subcategory="ambiguous_prompt_interpretation",
            probable_cause="vague prompt",
        )
        
        def custom_embedding(texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0], [0.0, 1.0]]
            
        clusterer = FailureClusteringEngine(embedding_fn=custom_embedding, min_cluster_size=2, eps=1.01)
        clusters = clusterer.cluster([f1, f2])
        
        self.assertEqual(len(clusters), 1)
        self.assertAlmostEqual(f1.cluster_confidence, 0.70710678)
        self.assertAlmostEqual(f2.cluster_confidence, 0.70710678)


if __name__ == "__main__":
    unittest.main()
