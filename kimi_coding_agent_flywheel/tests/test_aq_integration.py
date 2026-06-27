import sqlite3
import pytest
import tempfile
import sys
from pathlib import Path
from datetime import datetime

import agent_quality.db as aq_db
from agent_quality.capture.artifacts import write_artifact
from kimi_coding_agent_flywheel.core.aq_adapter import AQDbAdapter
from kimi_coding_agent_flywheel.clustering.failure_analyzer import FailureInstance, FailureCluster

def test_migration_adds_column(tmp_path):
    db_path = tmp_path / "old_schema.sqlite3"
    
    # 1. Create a database manually with the old schema (no provider_extensions column)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS failure_clusters (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            primary_category TEXT,
            severity TEXT,
            status TEXT NOT NULL,
            first_seen_at TEXT,
            last_seen_at TEXT,
            occurrence_count INTEGER DEFAULT 0,
            proposed_intervention TEXT,
            linked_regression_case TEXT
        );
    """)
    conn.commit()
    
    # Verify the column does NOT exist yet
    cursor = conn.execute("PRAGMA table_info(failure_clusters)")
    columns = [row[1] for row in cursor.fetchall()]
    assert "provider_extensions" not in columns
    conn.close()
    
    # 2. Call aq_db.connect(db_path) which triggers ensure_schema and migrations
    conn = aq_db.connect(db_path)
    
    # Verify the column now exists
    cursor = conn.execute("PRAGMA table_info(failure_clusters)")
    columns = [row[1] for row in cursor.fetchall()]
    assert "provider_extensions" in columns
    conn.close()


def test_foreign_keys_are_enforced(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    conn = aq_db.connect(db_path)
    
    # Attempt to insert a failure instance referencing non-existent run_id
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            aq_db.insert(
                conn,
                "failure_instances",
                {
                    "id": "fail_1",
                    "run_id": "non_existent_run_id",
                    "cluster_id": None,
                    "category": "specification",
                    "subcategory": "disobey_task_specification",
                    "description": "Violation description",
                    "severity": "high",
                    "probable_cause": "cause",
                    "suggested_fix": "fix",
                    "affected_prompt_component": "system_prompt",
                    "timestamp": datetime.utcnow().isoformat(),
                    "llm_judge_score": 5.0,
                }
            )
    conn.close()


def test_ingestion_redaction_removes_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    db_path = tmp_path / "test.sqlite3"
    adapter = AQDbAdapter(db_path)
    
    session_id = "session_1"
    run_id = "run_1"
    secret_key = "sk-proj-123456789012345678901234" # matches openai key pattern
    
    # Save session and run with secret
    adapter.save_session(
        session_id=session_id,
        repository_path=str(tmp_path),
        started_at=datetime.utcnow(),
        task_summary=f"Task with secret key {secret_key}",
    )
    
    adapter.save_run(
        run_id=run_id,
        session_id=session_id,
        turn_number=1,
        prompt=f"System prompt with secret {secret_key}",
        model="gpt-4o",
        started_at=datetime.utcnow(),
    )
    
    # Save artifact with secret
    adapter.save_artifact(
        run_id=run_id,
        artifact_type="debug_log",
        name="debug.txt",
        content=f"Log trace containing {secret_key} and some data",
    )
    
    # Query database and verify redaction occurred automatically on insert
    conn = adapter.connect()
    session_row = aq_db.one(conn, "SELECT task_summary FROM sessions WHERE id=?", [session_id])
    run_row = aq_db.one(conn, "SELECT prompt FROM runs WHERE id=?", [run_id])
    artifact_row = aq_db.one(conn, "SELECT path FROM artifacts WHERE run_id=?", [run_id])
    
    assert secret_key not in session_row["task_summary"]
    assert "[REDACTED:" in session_row["task_summary"]
    
    assert secret_key not in run_row["prompt"]
    assert "[REDACTED:" in run_row["prompt"]
    
    # Check artifact file content
    art_path = Path(artifact_row["path"])
    assert art_path.exists()
    art_content = art_path.read_text(encoding="utf-8")
    assert secret_key not in art_content
    assert "[REDACTED:" in art_content
    
    conn.close()


def test_save_clusters_transactional_atomicity(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    adapter = AQDbAdapter(db_path)
    
    # Insert prerequisite session and run
    adapter.save_session("session_1", str(tmp_path), datetime.utcnow(), "session summary")
    adapter.save_run("run_1", "session_1", 1, "prompt", "model", datetime.utcnow())
    
    # Initialize failure analysis run log
    analysis_id = "analysis_1"
    adapter.save_analysis_run(analysis_id, "DBSCAN")
    
    # Create failure cluster containing two failures
    # One failure task_id exists (run_1), one failure task_id is invalid (invalid_run) -> will trigger foreign key IntegrityError during save_clusters
    f1 = FailureInstance(
        failure_id="fail_f1",
        task_id="run_1",
        agent_name="kimi",
        category="specification",
        subcategory="disobey_task_specification",
        description="F1 description",
        severity="high",
        timestamp=datetime.utcnow(),
    )
    f2 = FailureInstance(
        failure_id="fail_f2",
        task_id="invalid_run", # This violates the FOREIGN KEY constraint in failure_cluster_memberships / failure_instances
        agent_name="kimi",
        category="specification",
        subcategory="disobey_task_specification",
        description="F2 description",
        severity="high",
        timestamp=datetime.utcnow(),
    )
    
    cluster = FailureCluster(
        cluster_id=1,
        label="Test Cluster",
        description="Cluster description",
        failures=[f1, f2],
        dominant_category="specification",
        dominant_subcategory="disobey_task_specification",
        common_keywords=["test"],
        avg_severity="high",
    )
    
    # Execute save_clusters which should fail and rollback atomically
    with pytest.raises(sqlite3.IntegrityError):
        adapter.save_clusters(analysis_id, [cluster])
        
    # Verify database state has no partial records (no cluster inserted)
    conn = adapter.connect()
    cluster_row = aq_db.one(conn, "SELECT COUNT(*) as count FROM failure_clusters")
    membership_row = aq_db.one(conn, "SELECT COUNT(*) as count FROM failure_cluster_memberships")
    instances_row = aq_db.one(conn, "SELECT COUNT(*) as count FROM failure_instances")
    
    assert cluster_row["count"] == 0
    assert membership_row["count"] == 0
    assert instances_row["count"] == 0
    conn.close()


def test_package_boundary_rules():
    # Scan agent-quality/src for any reference to kimi_coding_agent_flywheel
    src_dir = Path("agent-quality/src")
    assert src_dir.exists()
    
    violations = []
    for python_file in src_dir.glob("**/*.py"):
        content = python_file.read_text(encoding="utf-8")
        if "import kimi_coding_agent" in content or "from kimi_coding_agent" in content or "import kimi" in content or "from kimi" in content:
            violations.append(f"{python_file.name}: imports kimi")
                
    assert len(violations) == 0, f"Found boundary violations: {violations}"
