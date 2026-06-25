from __future__ import annotations

from pathlib import Path
from typing import Any

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
        _save_review(
            conn,
            run["id"],
            outcome,
            primary_category=primary,
            severity=severity,
            notes=notes,
            confidence=confidence,
            critical_sequence=critical,
        )
    print(f"stored review for {run['id']}")


def save_review_api(
    run_id: str,
    outcome: str,
    primary_category: str | None = None,
    severity: str | None = None,
    notes: str = "",
    confidence: float | None = None,
    critical_sequence: int | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    with connect(db_path) as conn:
        return _save_review(
            conn,
            run_id,
            outcome,
            primary_category=primary_category,
            severity=severity,
            notes=notes,
            confidence=confidence,
            critical_sequence=critical_sequence,
        )


def _save_review(
    conn,
    run_id: str,
    outcome: str,
    *,
    primary_category: str | None,
    severity: str | None,
    notes: str,
    confidence: float | None,
    critical_sequence: int | None,
) -> dict[str, Any]:
    if not one(conn, "SELECT id FROM runs WHERE id=?", [run_id]):
        raise ValueError(f"unknown run: {run_id}")

    reviewed_at = utc_now()
    values = {
        "outcome": outcome,
        "severity": _empty_to_none(severity),
        "primary_failure_category": _empty_to_none(primary_category),
        "confidence": confidence,
        "critical_event_sequence": critical_sequence,
        "notes": notes or "",
        "reviewed_at": reviewed_at,
    }
    existing = one(
        conn,
        "SELECT id FROM human_reviews WHERE run_id=? ORDER BY reviewed_at DESC, rowid DESC LIMIT 1",
        [run_id],
    )
    if existing:
        conn.execute(
            """
            UPDATE human_reviews
            SET outcome=?,
                severity=?,
                primary_failure_category=?,
                confidence=?,
                critical_event_sequence=?,
                notes=?,
                reviewed_at=?
            WHERE id=?
            """,
            [
                values["outcome"],
                values["severity"],
                values["primary_failure_category"],
                values["confidence"],
                values["critical_event_sequence"],
                values["notes"],
                values["reviewed_at"],
                existing["id"],
            ],
        )
        review_id = existing["id"]
    else:
        review_id = new_id("rev")
        insert(
            conn,
            "human_reviews",
            {
                "id": review_id,
                "run_id": run_id,
                "code_retention": None,
                "contributing_categories": None,
                **values,
            },
        )
    update_run(conn, run_id, human_status=outcome)
    return {"id": review_id, "run_id": run_id, **values}


def _empty_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
