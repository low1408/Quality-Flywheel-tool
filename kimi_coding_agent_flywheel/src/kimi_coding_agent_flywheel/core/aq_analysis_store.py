"""Persistence for analysis runs, failures, clusters, and memberships."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

import agent_quality.db as aq_db
import agent_quality.privacy.redaction as aq_redact
from agent_quality.timeutil import utc_now

from ..clustering.models import FailureCluster


class AQAnalysisStoreMixin:
    """Persist versioned failure-analysis results transactionally."""

    def save_analysis_run(
        self,
        analysis_id: str,
        algorithm: str,
        parameters: str | None = None,
        judge_version: str | None = None,
        redaction_version: str | None = None,
        status: str = "completed",
        selected_run_count: int = 0,
    ) -> None:
        """Insert or update a versioned failure analysis run log."""
        conn = self.connect()
        with conn:
            conn.execute(
                """
                INSERT INTO analysis_runs (
                    id, algorithm, parameters, judge_version, redaction_version, created_at,
                    status, selected_run_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    algorithm=excluded.algorithm,
                    parameters=excluded.parameters,
                    judge_version=excluded.judge_version,
                    redaction_version=excluded.redaction_version,
                    status=excluded.status,
                    selected_run_count=excluded.selected_run_count
                """,
                (
                    analysis_id,
                    algorithm,
                    parameters,
                    judge_version,
                    redaction_version,
                    utc_now(),
                    status,
                    selected_run_count,
                ),
            )

    def create_analysis_run(
        self,
        analysis_id: str,
        run_ids: list[str],
        *,
        parameters: dict[str, Any],
        judge_version: str,
    ) -> None:
        full_params = {
            "clustering_strategy": "root_cause_tfidf_v1",
            "feature_schema": "failure_features_v1",
            "algorithm": "dbscan",
            "metric": "cosine",
            "eps": 0.3,
            **parameters
        }
        self.save_analysis_run(
            analysis_id,
            "DBSCAN",
            parameters=json.dumps(full_params, sort_keys=True),
            judge_version=judge_version,
            redaction_version=aq_redact.POLICY_VERSION,
            status="running",
            selected_run_count=len(run_ids),
        )
        conn = self.connect()
        with conn:
            for run_id in run_ids:
                aq_db.insert(
                    conn,
                    "analysis_inputs",
                    {
                        "analysis_id": analysis_id,
                        "run_id": run_id,
                        "status": "pending",
                        "failure_count": 0,
                        "error_type": None,
                        "error_message": None,
                    },
                    or_action="OR REPLACE",
                )

    def update_analysis_input(
        self,
        analysis_id: str,
        run_id: str,
        *,
        status: str,
        failure_count: int = 0,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        conn = self.connect()
        with conn:
            conn.execute(
                """
                UPDATE analysis_inputs
                SET status=?, failure_count=?, error_type=?, error_message=?
                WHERE analysis_id=? AND run_id=?
                """,
                [status, failure_count, error_type, self._redact_text(error_message), analysis_id, run_id],
            )

    def finish_analysis_run(
        self,
        analysis_id: str,
        *,
        status: str,
        algorithm: str | None = None,
        failure_count: int = 0,
        cluster_count: int = 0,
        error_message: str | None = None,
    ) -> None:
        conn = self.connect()
        with conn:
            if algorithm:
                conn.execute(
                    """
                    UPDATE analysis_runs
                    SET status=?, completed_at=?, error_message=?, failure_count=?, cluster_count=?, algorithm=?
                    WHERE id=?
                    """,
                    [status, utc_now(), self._redact_text(error_message), failure_count, cluster_count, algorithm, analysis_id],
                )
            else:
                conn.execute(
                    """
                    UPDATE analysis_runs
                    SET status=?, completed_at=?, error_message=?, failure_count=?, cluster_count=?
                    WHERE id=?
                    """,
                    [status, utc_now(), self._redact_text(error_message), failure_count, cluster_count, analysis_id],
                )

    def save_failure_instance(
        self,
        conn: sqlite3.Connection,
        failure: Any,
        cluster_id: str | None = None,
        analysis_id: str | None = None,
    ) -> None:
        """Save a FailureInstance to the failure_instances SQLite table."""
        # Map timestamp to ISO format string safely
        ts_val = failure.timestamp.isoformat() if isinstance(failure.timestamp, datetime) else str(failure.timestamp)

        aq_db.insert(
            conn,
            "failure_instances",
            {
                "id": f"{analysis_id}:{failure.failure_id}" if analysis_id else failure.failure_id,
                "analysis_id": analysis_id,
                "run_id": failure.task_id,
                "cluster_id": cluster_id,
                "category": failure.category,
                "subcategory": failure.subcategory,
                "description": failure.description,
                "severity": failure.severity,
                "probable_cause": failure.probable_cause,
                "suggested_fix": failure.suggested_fix,
                "affected_prompt_component": failure.affected_prompt_component,
                "timestamp": ts_val,
                "llm_judge_score": failure.llm_judge_score,
            },
            or_action="OR REPLACE",
        )

    def save_failures(self, failures: list[Any], analysis_id: str | None = None) -> None:
        """Save a list of FailureInstance objects to SQLite."""
        conn = self.connect()
        with conn:
            for failure in failures:
                self.save_failure_instance(conn, failure, None, analysis_id)

    def persist_analysis_results(self, analysis_id: str, failures: list[Any], clusters: list[FailureCluster]) -> None:
        """Atomically persist all diagnosed failures, clusters, and memberships."""
        conn = self.connect()
        with conn:
            for failure in failures:
                self.save_failure_instance(conn, failure, None, analysis_id)
            self._save_clusters(conn, analysis_id, clusters)

    def save_clusters(self, analysis_id: str, clusters: list[FailureCluster]) -> None:
        """Persist FailureCluster definitions and map runs to cluster memberships in SQLite."""
        conn = self.connect()
        with conn:
            self._save_clusters(conn, analysis_id, clusters)

    def _save_clusters(self, conn: sqlite3.Connection, analysis_id: str, clusters: list[FailureCluster]) -> None:
        for cluster in clusters:
            # Stable cluster ID signature from dominant category, dominant subcategory, and affected prompt component
            sig_parts = [
                cluster.dominant_category or "unknown",
                cluster.dominant_subcategory or "unknown",
                cluster.failures[0].affected_prompt_component or "unknown" if cluster.failures else "unknown"
            ]
            sig = ":".join(sig_parts)
            import hashlib
            sig_hash = hashlib.sha256(sig.encode('utf-8')).hexdigest()[:12]
            cluster_id = f"cluster_{sig_hash}"

            existing = aq_db.one(conn, "SELECT id FROM failure_clusters WHERE id=?", [cluster_id])
            extra_payload = json.dumps(
                {
                    "dominant_subcategory": cluster.dominant_subcategory,
                    "common_keywords": cluster.common_keywords,
                    "common_tool_calls": cluster.common_tool_calls,
                    "regression_tests_needed": cluster.regression_tests_needed,
                }
            )

            if not existing:
                aq_db.insert(
                    conn,
                    "failure_clusters",
                    {
                        "id": cluster_id,
                        "title": cluster.label,
                        "description": cluster.description,
                        "primary_category": cluster.dominant_category,
                        "severity": cluster.avg_severity,
                        "status": "active",
                        "first_seen_at": utc_now(),
                        "last_seen_at": utc_now(),
                        "occurrence_count": len(cluster.failures),
                        "proposed_intervention": cluster.suggested_prompt_fix or cluster.suggested_tool_fix,
                        "linked_regression_case": None,
                        "provider_extensions": extra_payload,
                    },
                )
            else:
                conn.execute(
                    """
                    UPDATE failure_clusters SET
                        title=?, description=?, primary_category=?, severity=?,
                        proposed_intervention=?, provider_extensions=?, last_seen_at=?
                    WHERE id=?
                    """,
                    (
                        cluster.label,
                        cluster.description,
                        cluster.dominant_category,
                        cluster.avg_severity,
                        cluster.suggested_prompt_fix or cluster.suggested_tool_fix,
                        extra_payload,
                        utc_now(),
                        cluster_id,
                    ),
                )

            for failure in cluster.failures:
                self.save_failure_instance(conn, failure, cluster_id, analysis_id)
                confidence = getattr(failure, "cluster_confidence", None)
                if confidence is None:
                    confidence = float(failure.llm_judge_score or 10.0) / 10.0
                aq_db.insert(
                    conn,
                    "failure_cluster_memberships",
                    {
                        "analysis_id": analysis_id,
                        "run_id": failure.task_id,
                        "cluster_id": cluster_id,
                        "assignment_type": getattr(cluster, "assignment_type", "dbscan"),
                        "confidence": confidence,
                    },
                    or_action="OR REPLACE",
                )

            # Update count in a self-healing way
            conn.execute(
                """
                UPDATE failure_clusters
                SET occurrence_count=(SELECT COUNT(*) FROM failure_instances WHERE cluster_id=?)
                WHERE id=?
                """,
                [cluster_id, cluster_id]
            )
