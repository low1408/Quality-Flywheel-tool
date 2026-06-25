from __future__ import annotations

from agent_quality.db import all_rows, connect, one


def summary() -> None:
    conn = connect()
    total = one(conn, "SELECT COUNT(*) AS n FROM runs")["n"]
    completed = one(conn, "SELECT COUNT(*) AS n FROM runs WHERE agent_status='completed'")["n"]
    verifier_passed = one(conn, "SELECT COUNT(*) AS n FROM runs WHERE verifier_status='passed'")["n"]
    reviewed = one(
        conn,
        "SELECT COUNT(*) AS n FROM runs WHERE human_status IS NOT NULL AND human_status NOT IN ('not_reviewed','review_skipped')",
    )["n"]
    accepted = one(
        conn,
        "SELECT COUNT(*) AS n FROM runs WHERE human_status IN ('accepted_cleanly','accepted_with_minor_edits','accepted_with_major_edits')",
    )["n"]
    print(f"runs: {total}")
    print(f"completed: {completed}")
    print(f"verified_passed: {verifier_passed}")
    print(f"reviewed: {reviewed}")
    print(f"accepted: {accepted}")
    print(f"verified_pass_rate: {_rate(verifier_passed, completed)}")
    print(f"human_acceptance_rate: {_rate(accepted, reviewed)}")
    print("\nrecent runs:")
    for run in all_rows(conn, "SELECT id, agent_status, verifier_status, human_status, started_at FROM runs ORDER BY started_at DESC LIMIT 10"):
        print(f"  {run['started_at']} {run['id']} agent={run['agent_status']} verifier={run['verifier_status']} human={run['human_status']}")


def _rate(num: int, den: int) -> str:
    return "n/a" if den == 0 else f"{num / den:.3f}"
