from __future__ import annotations

from agent_quality.db import all_rows, connect, insert, one, update_run
from agent_quality.ids import new_id
from agent_quality.review.labels import FAILURE_CATEGORIES, OUTCOMES
from agent_quality.timeutil import utc_now


def next_review_run(conn):
    return one(
        conn,
        "SELECT * FROM runs WHERE human_status IS NULL OR human_status='not_reviewed' ORDER BY started_at DESC LIMIT 1",
    )


def review_run(run_id: str | None = None) -> None:
    conn = connect()
    run = one(conn, "SELECT * FROM runs WHERE id=?", [run_id]) if run_id else next_review_run(conn)
    if not run:
        print("no run pending review")
        return
    changed = all_rows(conn, "SELECT artifact_type, path FROM artifacts WHERE run_id=? ORDER BY artifact_type", [run["id"]])
    print(f"Run: {run['id']}")
    print(f"Automatic verification: {run['verifier_status']}")
    print(f"Duration: {run['duration_ms']} ms")
    print("Artifacts:")
    for artifact in changed:
        print(f"  {artifact['artifact_type']}: {artifact['path']}")
    print("\nHuman outcome:")
    for key, value in OUTCOMES.items():
        print(f"  [{key}] {value}")
    outcome = OUTCOMES.get(input("> ").strip(), "not_reviewed")
    primary = None
    severity = None
    confidence = None
    critical = None
    notes = ""
    if outcome not in ("accepted_cleanly", "not_reviewed"):
        print("Primary failure category:")
        for key, value in FAILURE_CATEGORIES.items():
            print(f"  [{key}] {value}")
        primary = FAILURE_CATEGORIES.get(input("> ").strip(), "unknown")
        severity = input("Severity [low/medium/high/critical]: ").strip() or None
        raw_confidence = input("Confidence [0.0-1.0]: ").strip()
        confidence = float(raw_confidence) if raw_confidence else None
        raw_critical = input("Critical event sequence: ").strip()
        critical = int(raw_critical) if raw_critical.isdigit() else None
        notes = input("Notes: ").strip()
    with conn:
        insert(
            conn,
            "human_reviews",
            {
                "id": new_id("rev"),
                "run_id": run["id"],
                "outcome": outcome,
                "code_retention": None,
                "severity": severity,
                "primary_failure_category": primary,
                "contributing_categories": None,
                "confidence": confidence,
                "critical_event_sequence": critical,
                "notes": notes,
                "reviewed_at": utc_now(),
            },
        )
        update_run(conn, run["id"], human_status=outcome)
    print(f"stored review for {run['id']}")
