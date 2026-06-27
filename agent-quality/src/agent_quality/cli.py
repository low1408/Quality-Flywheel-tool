from __future__ import annotations

import argparse
import json
import shlex
import sqlite3
import sys
from pathlib import Path

from agent_quality.adapters.codex_hooks import main as codex_hook_main
from agent_quality.collector.envelope import normalize_envelope
from agent_quality.collector.server import serve
from agent_quality.db import all_rows, connect, insert, one
from agent_quality.orchestrator import run_task
from agent_quality.regressions.registry import promote
from agent_quality.reports.metrics import summary
from agent_quality.review.service import review_run


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="aq")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--repo", default=".")

    run = sub.add_parser("run")
    run.add_argument("prompt")
    run.add_argument("--repo", default=".")
    run.add_argument("--verify")
    run.add_argument("--session")
    run.add_argument("--allow-dirty", action="store_true")
    run.add_argument("--model")
    run.add_argument("--skip-review", action="store_true", help="record the run but exclude it from human review queues")
    run.add_argument("--agent-timeout-seconds", type=int, default=600)
    run.add_argument("--agent-command", nargs=argparse.REMAINDER)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--file")

    hook = sub.add_parser("hook")
    hook_sub = hook.add_subparsers(dest="hook", required=True)
    codex_hook = hook_sub.add_parser("codex")
    codex_hook.add_argument("event")

    install_hooks = sub.add_parser("install-codex-hooks")
    install_hooks.add_argument("--repo", default=".")
    install_hooks.add_argument("--python", default=sys.executable)

    server = sub.add_parser("serve-collector")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument("--token")

    review = sub.add_parser("review")
    review.add_argument("run_id", nargs="?")

    show = sub.add_parser("show")
    show.add_argument("run_id")

    diff = sub.add_parser("diff")
    diff.add_argument("run_id")

    trace = sub.add_parser("trace")
    trace.add_argument("run_id")

    promote_cmd = sub.add_parser("promote")
    promote_cmd.add_argument("run_id")
    promote_cmd.add_argument("--case-id", required=True)

    report = sub.add_parser("report")
    report_sub = report.add_subparsers(dest="report", required=True)
    report_sub.add_parser("summary")

    args = parser.parse_args(argv)
    if args.command == "init":
        _init_project(Path(args.repo))
    elif args.command == "run":
        run_task(
            prompt=args.prompt,
            repo=Path(args.repo),
            verify_path=Path(args.verify) if args.verify else None,
            session_id=args.session,
            allow_dirty=args.allow_dirty,
            model=args.model,
            agent_command=args.agent_command or None,
            skip_review=args.skip_review,
            agent_timeout_seconds=args.agent_timeout_seconds,
        )
    elif args.command == "ingest":
        _ingest(args.file)
    elif args.command == "hook" and args.hook == "codex":
        raise SystemExit(codex_hook_main([args.event]))
    elif args.command == "install-codex-hooks":
        _install_codex_hooks(Path(args.repo), args.python)
    elif args.command == "serve-collector":
        serve(args.host, args.port, token=args.token)
    elif args.command == "review":
        review_run(args.run_id)
    elif args.command == "show":
        _show(args.run_id)
    elif args.command == "diff":
        _print_artifact(args.run_id, "final_patch")
    elif args.command == "trace":
        _trace(args.run_id)
    elif args.command == "promote":
        case_dir = promote(args.run_id, args.case_id)
        print(case_dir)
    elif args.command == "report" and args.report == "summary":
        summary()


def _init_project(repo: Path) -> None:
    repo = _project_root(repo)
    aq = repo / ".agent-quality"
    (aq / "cases").mkdir(parents=True, exist_ok=True)
    verify = aq / "verify.yaml"
    protected = aq / "protected-paths.txt"
    config = aq / "config.yaml"
    if not verify.exists():
        verify.write_text(
            "\n".join(
                [
                    "version: 1",
                    "acceptance: []",
                    "regression: []",
                    "static: []",
                    "protected_paths:",
                    "  - .agent-quality/**",
                    "trajectory:",
                    "  require_test_after_final_edit: true",
                    "  max_identical_failed_commands: 1",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    if not protected.exists():
        protected.write_text(".agent-quality/**\n", encoding="utf-8")
    if not config.exists():
        config.write_text("version: 1\n", encoding="utf-8")
    print(f"initialized {aq}")


def _install_codex_hooks(repo: Path, python: str) -> None:
    repo = _project_root(repo)
    codex_dir = repo / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    aq_home = repo / ".agent-quality" / "local"
    aq_home.mkdir(parents=True, exist_ok=True)
    tool_src = Path(__file__).resolve().parents[1]
    command = (
        f"AGENT_QUALITY_HOME={shlex.quote(str(aq_home))} "
        f"PYTHONPATH={shlex.quote(str(tool_src))} "
        f"{shlex.quote(python)} -m agent_quality.cli hook codex"
    )
    hooks = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": f"{command} SessionStart"}]}],
            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": f"{command} UserPromptSubmit"}]}],
            "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": f"{command} PreToolUse"}]}],
            "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": f"{command} PostToolUse"}]}],
            "PermissionRequest": [{"matcher": "*", "hooks": [{"type": "command", "command": f"{command} PermissionRequest"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": f"{command} Stop", "timeout": 30}]}],
        }
    }
    (codex_dir / "hooks.json").write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")
    _ensure_codex_hooks_enabled(codex_dir / "config.toml")
    print(f"installed Codex hooks: {codex_dir / 'hooks.json'}")
    print(f"enabled Codex hooks: {codex_dir / 'config.toml'}")


def _project_root(repo: Path) -> Path:
    path = repo.expanduser().resolve()
    if path.is_file():
        path = path.parent
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return path


def _ensure_codex_hooks_enabled(config_path: Path) -> None:
    if not config_path.exists():
        config_path.write_text("[features]\nhooks = true\n", encoding="utf-8")
        return

    lines = config_path.read_text(encoding="utf-8").splitlines()
    features_start: int | None = None
    next_table = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[features]":
            features_start = index
            continue
        if features_start is not None and index > features_start and stripped.startswith("[") and stripped.endswith("]"):
            next_table = index
            break

    if features_start is None:
        suffix = "" if not lines or lines[-1] == "" else "\n"
        config_path.write_text("\n".join(lines) + f"{suffix}\n[features]\nhooks = true\n", encoding="utf-8")
        return

    for index in range(features_start + 1, next_table):
        stripped = lines[index].strip()
        if "=" in stripped and stripped.split("=", 1)[0].strip() == "hooks":
            indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
            lines[index] = f"{indent}hooks = true"
            config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return

    lines.insert(features_start + 1, "hooks = true")
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ingest(path: str | None) -> None:
    conn = connect()
    source = Path(path).read_text(encoding="utf-8").splitlines() if path else sys.stdin.read().splitlines()
    count = 0
    skipped = 0
    for line_number, line in enumerate(source, start=1):
        if not line.strip():
            continue
        try:
            row = normalize_envelope(json.loads(line))
            with conn:
                insert(conn, "events", row)
            count += 1
        except json.JSONDecodeError as exc:
            skipped += 1
            print(f"skipped line {line_number}: invalid JSON: {exc}", file=sys.stderr)
        except sqlite3.IntegrityError as exc:
            skipped += 1
            reason = "duplicate event" if _is_unique_constraint(exc) else f"integrity error: {exc}"
            print(f"skipped line {line_number}: {reason}", file=sys.stderr)
    suffix = "" if skipped == 0 else f", skipped {skipped}"
    print(f"ingested {count} events{suffix}")


def _is_unique_constraint(exc: sqlite3.IntegrityError) -> bool:
    if getattr(exc, "sqlite_errorname", "") in {"SQLITE_CONSTRAINT_UNIQUE", "SQLITE_CONSTRAINT_PRIMARYKEY"}:
        return True
    return "UNIQUE constraint failed" in str(exc)


def _show(run_id: str) -> None:
    conn = connect()
    run = one(conn, "SELECT * FROM runs WHERE id=?", [run_id])
    if not run:
        raise SystemExit(f"unknown run: {run_id}")
    for key in run.keys():
        print(f"{key}: {run[key]}")
    print("artifacts:")
    for artifact in all_rows(conn, "SELECT artifact_type, path FROM artifacts WHERE run_id=? ORDER BY artifact_type", [run_id]):
        print(f"  {artifact['artifact_type']}: {artifact['path']}")
    print("verifiers:")
    for result in all_rows(conn, "SELECT verifier_name, verifier_category, passed, exit_code FROM verifier_results WHERE run_id=?", [run_id]):
        print(f"  {result['verifier_category']} {result['verifier_name']} passed={bool(result['passed'])} exit={result['exit_code']}")


def _print_artifact(run_id: str, artifact_type: str) -> None:
    conn = connect()
    artifact = one(conn, "SELECT path FROM artifacts WHERE run_id=? AND artifact_type=? ORDER BY rowid DESC LIMIT 1", [run_id, artifact_type])
    if not artifact:
        raise SystemExit(f"no {artifact_type} artifact for {run_id}")
    print(Path(artifact["path"]).read_text(encoding="utf-8"))


def _trace(run_id: str) -> None:
    conn = connect()
    rows = all_rows(
        conn,
        "SELECT sequence_number, event_type, status, item_type, tool_category, command, path, exit_code FROM events WHERE run_id=? ORDER BY sequence_number",
        [run_id],
    )
    for row in rows:
        detail = row["command"] or row["path"] or ""
        exit_text = "" if row["exit_code"] is None else f" exit={row['exit_code']}"
        print(f"{row['sequence_number']:>4} {row['event_type']} {row['status'] or ''} {row['tool_category'] or ''}{exit_text} {detail}")


if __name__ == "__main__":
    main()
