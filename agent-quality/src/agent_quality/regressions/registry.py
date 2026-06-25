from __future__ import annotations

from pathlib import Path

from agent_quality.db import all_rows, connect, one


def promote(run_id: str, case_id: str, cases_dir: Path | None = None) -> Path:
    conn = connect()
    run = one(conn, "SELECT * FROM runs WHERE id=?", [run_id])
    if not run:
        raise SystemExit(f"unknown run: {run_id}")
    root = cases_dir or Path(run["repository_path"]) / ".agent-quality" / "cases"
    case_dir = root / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "prompt.md").write_text(run["prompt"] or "", encoding="utf-8")
    artifacts = {row["artifact_type"]: row["path"] for row in all_rows(conn, "SELECT artifact_type, path FROM artifacts WHERE run_id=?", [run_id])}
    if "final_patch" in artifacts:
        (case_dir / "reference.patch").write_text(Path(artifacts["final_patch"]).read_text(encoding="utf-8"), encoding="utf-8")
    (case_dir / "case.yaml").write_text(
        "\n".join(
            [
                f"id: {case_id}",
                "version: 1",
                f"repository: {run['repository_path']}",
                f"base_commit: {run['base_commit']}",
                "source:",
                "  type: promoted_run",
                f"  run_id: {run_id}",
                "task:",
                "  prompt_file: prompt.md",
                "verification:",
                "  script: verify.sh",
                "expected:",
                "  acceptance_required: true",
                "  regression_required: true",
                "  protected_paths_clean: true",
                "repetitions: 1",
                "tags: []",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (case_dir / "verify.sh").write_text("#!/usr/bin/env bash\nset -euo pipefail\n# Fill in project-specific checks.\n", encoding="utf-8")
    return case_dir
