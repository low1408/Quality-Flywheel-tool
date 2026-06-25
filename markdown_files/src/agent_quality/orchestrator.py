from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from agent_quality import __version__
from agent_quality.adapters.codex_cli import extract_usage, rows_from_jsonl
from agent_quality.capture.artifacts import write_artifact
from agent_quality.capture.git_state import diff, file_hash_if_exists, head_commit, repo_root, status_porcelain
from agent_quality.config import load_verify_config
from agent_quality.db import connect, insert, mark_session_ended, update_run
from agent_quality.hashutil import sha256_text
from agent_quality.ids import new_id
from agent_quality.timeutil import utc_now
from agent_quality.verification.protected_paths import changed_paths_from_name_status, protected_patterns, protected_violations
from agent_quality.verification.runner import run_verifiers


def run_task(
    *,
    prompt: str,
    repo: Path,
    verify_path: Path | None = None,
    session_id: str | None = None,
    allow_dirty: bool = False,
    model: str | None = None,
    agent_command: list[str] | None = None,
) -> str:
    repo = repo_root(repo)
    before_status = status_porcelain(repo)
    if before_status and not allow_dirty:
        raise SystemExit("repository is dirty; rerun with --allow-dirty if this is intentional")

    conn = connect()
    run_id = new_id("run")
    session_id = session_id or new_id("ses")
    started_at = utc_now()
    base_commit = head_commit(repo)
    verify_config = load_verify_config(verify_path)
    prompt_hash = sha256_text(prompt)
    begin = time.monotonic()

    with conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO sessions (
                id, repository_path, repository_remote_hash, started_at, ended_at, final_outcome, task_summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, str(repo), None, started_at, None, None, prompt[:240]),
        )
        insert(
            conn,
            "runs",
            {
                "id": run_id,
                "session_id": session_id,
                "turn_number": 1,
                "prompt": prompt,
                "prompt_hash": prompt_hash,
                "repository_path": str(repo),
                "base_commit": base_commit,
                "resulting_commit": None,
                "model": model,
                "agent_adapter": "codex-cli",
                "agent_version": _codex_version(),
                "wrapper_version": __version__,
                "codex_config_hash": file_hash_if_exists(repo, ".codex/config.toml"),
                "agents_md_hash": file_hash_if_exists(repo, "AGENTS.md"),
                "verifier_version": sha256_text(json.dumps(verify_config, sort_keys=True)),
                "started_at": started_at,
                "completed_at": None,
                "duration_ms": None,
                "agent_status": "created",
                "verifier_status": None,
                "human_status": "not_reviewed",
                "lifecycle_status": "still_open",
                "input_tokens": None,
                "cached_input_tokens": None,
                "output_tokens": None,
            },
        )
        _store_artifact(conn, run_id, "prompt", "prompt.txt", prompt)
        _store_artifact(conn, run_id, "before_status", "before-status.txt", before_status)

    command = agent_command or ["codex", "exec", "--json", "--sandbox", "workspace-write", prompt]
    if model and not agent_command:
        command = ["codex", "exec", "--json", "--model", model, "--sandbox", "workspace-write", prompt]

    with conn:
        update_run(conn, run_id, agent_status="running")

    stdout = ""
    stderr = ""
    exit_code = 127
    try:
        proc = subprocess.run(command, cwd=repo, text=True, capture_output=True)
        stdout, stderr, exit_code = proc.stdout, proc.stderr, proc.returncode
    except FileNotFoundError as exc:
        stderr = str(exc)

    raw_lines = stdout.splitlines()
    rows = rows_from_jsonl(raw_lines, run_id=run_id, session_id=session_id)
    with conn:
        for row in rows:
            insert(conn, "events", row)
        _store_artifact(conn, run_id, "events_jsonl", "events.jsonl", stdout)
        _store_artifact(conn, run_id, "stderr", "stderr.txt", stderr)

    after_status = status_porcelain(repo)
    final_patch = diff(repo, "--binary")
    name_status = diff(repo, "--name-status")
    with conn:
        _store_artifact(conn, run_id, "after_status", "after-status.txt", after_status)
        _store_artifact(conn, run_id, "final_patch", "final.patch", final_patch)
        _store_artifact(conn, run_id, "environment_manifest", "environment.json", _environment_manifest(repo, command))

    verifier = run_verifiers(conn, run_id=run_id, repo=repo, config=verify_config)
    violations = protected_violations(changed_paths_from_name_status(name_status), protected_patterns(verify_config))
    verifier_status = "failed" if violations else verifier.status
    if violations:
        with conn:
            _store_artifact(conn, run_id, "verifier_log", "protected-paths.txt", "\n".join(violations) + "\n")

    input_tokens, cached_input_tokens, output_tokens = extract_usage(raw_lines)
    duration_ms = int((time.monotonic() - begin) * 1000)
    with conn:
        update_run(
            conn,
            run_id,
            completed_at=utc_now(),
            duration_ms=duration_ms,
            agent_status="completed" if exit_code == 0 else "failed",
            verifier_status=verifier_status,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
        )
        mark_session_ended(conn, session_id)
    print(f"run_id={run_id}")
    print(f"agent_status={'completed' if exit_code == 0 else 'failed'} verifier_status={verifier_status}")
    return run_id


def _store_artifact(conn, run_id: str, artifact_type: str, name: str, content: str) -> None:
    artifact_id, path, digest, size = write_artifact(run_id, name, content)
    insert(
        conn,
        "artifacts",
        {
            "id": artifact_id,
            "run_id": run_id,
            "artifact_type": artifact_type,
            "path": str(path),
            "sha256": digest,
            "size_bytes": size,
        },
    )


def _codex_version() -> str | None:
    if not shutil.which("codex"):
        return None
    proc = subprocess.run(["codex", "--version"], text=True, capture_output=True)
    return (proc.stdout or proc.stderr).strip() or None


def _environment_manifest(repo: Path, command: list[str]) -> str:
    return json.dumps(
        {
            "repo": str(repo),
            "head": head_commit(repo),
            "command": command,
            "python": subprocess.run(["python3", "--version"], text=True, capture_output=True).stdout.strip(),
        },
        indent=2,
        sort_keys=True,
    )
