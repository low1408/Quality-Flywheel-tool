from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from .clustering.failure_analyzer import (
    DiagnosisInfrastructureError,
    FailureAnalysisPipeline,
    FailureClusteringEngine,
    LLMJudgeDiagnoser,
)
from .core.aq_adapter import AQDbAdapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aq-flywheel")
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="Diagnose and cluster existing Agent Quality runs")
    analyze.add_argument("--db", required=True)
    analyze.add_argument("--run-id", action="append", required=True, dest="run_ids")
    analyze.add_argument("--min-cluster-size", type=int, default=2)
    analyze.add_argument("--judge-command-json", required=True)
    analyze.add_argument("--judge-timeout", type=float, default=120.0)
    return parser


def _emit(event_type: str, **payload: Any) -> None:
    print(json.dumps({"type": event_type, **payload}, sort_keys=True), flush=True)


def _parse_judge_command(raw: str) -> list[str]:
    try:
        command = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"judge command is not valid JSON: {exc.msg}") from exc
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg for arg in command):
        raise ValueError("judge command must be a non-empty JSON array of non-empty strings")
    return command


def _judge_function(command: list[str], timeout: float):
    def judge(prompt: str) -> str:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()
            raise RuntimeError(detail[-1] if detail else f"judge exited with status {completed.returncode}")
        return completed.stdout

    return judge


def analyze(args: argparse.Namespace) -> int:
    command = _parse_judge_command(args.judge_command_json)
    if args.min_cluster_size < 2:
        raise ValueError("min cluster size must be at least 2")
    run_ids = list(dict.fromkeys(args.run_ids))
    adapter = AQDbAdapter(Path(args.db))
    traces = adapter.load_run_traces(run_ids)
    analysis_id = f"analysis_{uuid.uuid4().hex}"
    adapter.create_analysis_run(
        analysis_id,
        run_ids,
        parameters={"min_cluster_size": args.min_cluster_size},
        judge_version=Path(command[0]).name,
    )
    _emit("analysis_started", analysis_id=analysis_id, total=len(traces))

    try:
        pipeline = FailureAnalysisPipeline(
            diagnoser=LLMJudgeDiagnoser(judge_fn=_judge_function(command, args.judge_timeout)),
            clusterer=FailureClusteringEngine(min_cluster_size=args.min_cluster_size),
        )
        for index, trace in enumerate(traces, start=1):
            failure_start = len(pipeline.all_failures)
            error_start = len(pipeline.diagnosis_errors)
            pipeline.process_traces([trace])
            new_failures = pipeline.all_failures[failure_start:]
            new_errors = pipeline.diagnosis_errors[error_start:]
            if new_errors:
                error = new_errors[-1]
                error_type = (
                    error.exception_type
                    if isinstance(error, DiagnosisInfrastructureError)
                    else "invalid_response"
                )
                adapter.update_analysis_input(
                    analysis_id,
                    trace["trace_id"],
                    status="failed",
                    error_type=error_type,
                    error_message=error.message,
                )
            else:
                adapter.update_analysis_input(
                    analysis_id,
                    trace["trace_id"],
                    status="completed",
                    failure_count=len(new_failures),
                )
            _emit(
                "analysis_progress",
                analysis_id=analysis_id,
                completed=index,
                total=len(traces),
                run_id=trace["trace_id"],
                failure_count=len(new_failures),
                error=bool(new_errors),
            )

        clusters = pipeline.run_clustering()
        adapter.persist_analysis_results(analysis_id, pipeline.all_failures, clusters)
        status = "completed_with_errors" if pipeline.diagnosis_errors else "completed"
        adapter.finish_analysis_run(
            analysis_id,
            status=status,
            algorithm=pipeline.clusterer.algorithm_used,
            failure_count=len(pipeline.all_failures),
            cluster_count=len(clusters),
        )
        _emit(
            "analysis_complete",
            analysis_id=analysis_id,
            status=status,
            selected_run_count=len(run_ids),
            failure_count=len(pipeline.all_failures),
            cluster_count=len(clusters),
            error_count=len(pipeline.diagnosis_errors),
        )
        return 0
    except Exception as exc:
        adapter.finish_analysis_run(analysis_id, status="failed", error_message=str(exc))
        _emit("analysis_failed", analysis_id=analysis_id, error=str(exc))
        return 1


def _main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            return analyze(args)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    parser.error("unknown command")
    return 2


def main() -> None:
    raise SystemExit(_main())


if __name__ == "__main__":
    main()
