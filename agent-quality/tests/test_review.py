from __future__ import annotations

from agent_quality.db import all_rows, connect, insert, one
from agent_quality.reports.metrics import summary
from agent_quality.review.service import next_review_run, save_review_api


def test_review_queue_ignores_skipped_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path))
    conn = connect()
    with conn:
        _insert_run(conn, "run_skipped", "review_skipped", "2026-01-02T00:00:00.000Z")
        _insert_run(conn, "run_pending", "not_reviewed", "2026-01-01T00:00:00.000Z")

    assert next_review_run(conn)["id"] == "run_pending"


def test_summary_does_not_count_skipped_runs_as_reviewed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path))
    conn = connect()
    with conn:
        _insert_run(conn, "run_skipped", "review_skipped", "2026-01-01T00:00:00.000Z")

    summary()

    out = capsys.readouterr().out
    assert "reviewed: 0\n" in out
    assert "human_acceptance_rate: n/a\n" in out


def test_save_review_api_updates_existing_review(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_HOME", str(tmp_path))
    conn = connect()
    with conn:
        _insert_run(conn, "run_reviewed", "not_reviewed", "2026-01-01T00:00:00.000Z")

    save_review_api(
        "run_reviewed",
        "rejected",
        primary_category="implementation",
        severity="high",
        notes="first pass",
        confidence=0.4,
        critical_sequence=3,
    )
    save_review_api(
        "run_reviewed",
        "accepted_cleanly",
        primary_category=None,
        severity=None,
        notes="edited",
        confidence=0.9,
        critical_sequence=None,
    )

    rows = all_rows(conn, "SELECT * FROM human_reviews WHERE run_id=?", ["run_reviewed"])
    run = one(conn, "SELECT human_status FROM runs WHERE id=?", ["run_reviewed"])
    assert len(rows) == 1
    assert rows[0]["outcome"] == "accepted_cleanly"
    assert rows[0]["notes"] == "edited"
    assert rows[0]["primary_failure_category"] is None
    assert run["human_status"] == "accepted_cleanly"


def _insert_run(conn, run_id: str, human_status: str, started_at: str) -> None:
    insert(
        conn,
        "runs",
        {
            "id": run_id,
            "session_id": None,
            "turn_number": 1,
            "prompt": "test",
            "prompt_hash": "hash",
            "repository_path": "/repo",
            "base_commit": "abc123",
            "resulting_commit": None,
            "model": None,
            "agent_adapter": "codex-cli",
            "agent_version": None,
            "wrapper_version": "test",
            "codex_config_hash": None,
            "agents_md_hash": None,
            "verifier_version": "verifier",
            "started_at": started_at,
            "completed_at": started_at,
            "duration_ms": 1,
            "agent_status": "completed",
            "verifier_status": "not_configured",
            "human_status": human_status,
            "lifecycle_status": "still_open",
            "input_tokens": None,
            "cached_input_tokens": None,
            "output_tokens": None,
        },
    )
