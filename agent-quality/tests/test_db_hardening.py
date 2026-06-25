import sqlite3

import pytest

from agent_quality.db import connect, insert, update_run


def test_insert_rejects_non_allowlisted_table(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    conn = connect(tmp_path / "quality.sqlite3")

    with pytest.raises(ValueError):
        insert(conn, "events; DROP TABLE runs--", {"id": "evt_1"})

    conn.execute("SELECT COUNT(*) FROM runs").fetchone()


def test_update_run_rejects_non_allowlisted_column(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    conn = connect(tmp_path / "quality.sqlite3")

    with pytest.raises(ValueError):
        update_run(conn, "run_1", **{"agent_status='failed' --": "x"})


def test_foreign_keys_are_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    conn = connect(tmp_path / "quality.sqlite3")

    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            insert(
                conn,
                "artifacts",
                {
                    "id": "art_1",
                    "run_id": "missing_run",
                    "artifact_type": "stderr",
                    "path": "/tmp/stderr.txt",
                    "sha256": "abc",
                    "size_bytes": 1,
                },
            )


def test_run_id_indexes_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path / "aq"))
    conn = connect(tmp_path / "quality.sqlite3")

    indexes = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchall()
    }

    assert "idx_events_run_id" in indexes
    assert "idx_artifacts_run_id" in indexes
    assert "idx_verifier_results_run_id" in indexes
    assert "idx_human_reviews_run_id" in indexes
