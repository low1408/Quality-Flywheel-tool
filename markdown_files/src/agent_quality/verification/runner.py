from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from agent_quality.capture.artifacts import write_artifact
from agent_quality.config import verifier_commands
from agent_quality.db import insert
from agent_quality.ids import new_id
from agent_quality.timeutil import utc_now


@dataclass(frozen=True)
class VerificationSummary:
    status: str
    passed: int
    failed: int


def run_verifiers(conn, *, run_id: str, repo: Path, config: dict) -> VerificationSummary:
    commands = verifier_commands(config)
    if not commands:
        return VerificationSummary("not_configured", 0, 0)

    passed = 0
    failed = 0
    for command in commands:
        started = utc_now()
        begin = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                command["command"],
                cwd=repo,
                shell=True,
                text=True,
                capture_output=True,
                timeout=command["timeout_seconds"],
            )
            exit_code = proc.returncode
            stdout = proc.stdout
            stderr = proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = 124
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr if isinstance(exc.stderr, str) else "") + "\n[agent-quality] verifier timed out"
        duration_ms = int((time.monotonic() - begin) * 1000)
        ok = exit_code == 0 and not timed_out
        passed += int(ok)
        failed += int(not ok)

        out_id, out_path, _, _ = write_artifact(run_id, f"verifier-{command['name']}-stdout.txt", stdout or "")
        err_id, err_path, _, _ = write_artifact(run_id, f"verifier-{command['name']}-stderr.txt", stderr or "")
        insert(
            conn,
            "verifier_results",
            {
                "id": new_id("ver"),
                "run_id": run_id,
                "verifier_name": command["name"],
                "verifier_category": command["category"],
                "command": command["command"],
                "started_at": started,
                "duration_ms": duration_ms,
                "exit_code": exit_code,
                "passed": 1 if ok else 0,
                "stdout_path": str(out_path),
                "stderr_path": str(err_path),
            },
        )
    return VerificationSummary("passed" if failed == 0 else "failed", passed, failed)
