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


if __name__ == "__main__":
    unittest.main()
