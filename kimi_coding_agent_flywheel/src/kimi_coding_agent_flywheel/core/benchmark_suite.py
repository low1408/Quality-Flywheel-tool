"""Benchmark suite orchestration and persistence."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..eval.evaluators import Evaluator
from .agents import CodingAgent
from .benchmark_models import AgentOutput, BenchmarkTask, Difficulty, EvaluationResult, TaskType

# -----------------------------------------------------------------------------
# Benchmark Suite
# -----------------------------------------------------------------------------

class BenchmarkSuite:
    """
    A collection of benchmark tasks with execution and evaluation capabilities.

    This is the primary interface for running evaluations and collecting
    results for the quality flywheel.
    """

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.tasks: dict[str, BenchmarkTask] = {}
        self.results: list[EvaluationResult] = []
        self._evaluators: dict[str, Evaluator] = {}

    def add_task(self, task: BenchmarkTask) -> None:
        """Add a task to the suite."""
        self.tasks[str(task.task_id)] = task

    def add_tasks(self, tasks: list[BenchmarkTask]) -> None:
        for task in tasks:
            self.add_task(task)

    def register_evaluator(self, task_type: TaskType, evaluator: Evaluator) -> None:
        """Register an evaluator for a specific task type."""
        self._evaluators[task_type.name] = evaluator

    def get_tasks_by_type(self, task_type: TaskType) -> list[BenchmarkTask]:
        return [t for t in self.tasks.values() if t.task_type == task_type]

    def get_tasks_by_difficulty(self, difficulty: Difficulty) -> list[BenchmarkTask]:
        return [t for t in self.tasks.values() if t.difficulty == difficulty]

    def get_tasks_by_tag(self, tag: str) -> list[BenchmarkTask]:
        return [t for t in self.tasks.values() if tag in t.tags]

    async def run_evaluation(
        self,
        agent: CodingAgent,
        task_filter: Callable[[BenchmarkTask], bool] | None = None,
        max_concurrent: int = 4,
        timeout_per_task: int = 300,
    ) -> list[EvaluationResult]:
        """
        Run the full benchmark suite against an agent.

        Args:
            agent: The coding agent to evaluate
            task_filter: Optional filter function for selecting tasks
            max_concurrent: Maximum parallel evaluations
            timeout_per_task: Seconds before aborting a task

        Returns:
            List of evaluation results
        """
        tasks = list(self.tasks.values())
        if task_filter:
            tasks = [t for t in tasks if task_filter(t)]

        semaphore = asyncio.Semaphore(max_concurrent)
        results: list[EvaluationResult] = []

        async def _run_single(task: BenchmarkTask) -> EvaluationResult:
            async with semaphore:
                try:
                    return await asyncio.wait_for(
                        self._evaluate_single(agent, task),
                        timeout=timeout_per_task,
                    )
                except asyncio.TimeoutError:
                    return EvaluationResult(
                        task_id=task.task_id,
                        agent_name=agent.name,
                        model_id=agent.model_id,
                        passed=False,
                        score=0.0,
                        failure_category="TIMEOUT",
                        failure_description=f"Task exceeded {timeout_per_task}s timeout",
                    )
                except Exception as e:
                    return EvaluationResult(
                        task_id=task.task_id,
                        agent_name=agent.name,
                        model_id=agent.model_id,
                        passed=False,
                        score=0.0,
                        failure_category="EXECUTION_ERROR",
                        failure_description=str(e),
                    )

        # Run all evaluations
        tasks_pending = [_run_single(t) for t in tasks]
        results = await asyncio.gather(*tasks_pending, return_exceptions=True)

        # Filter out exceptions
        clean_results: list[EvaluationResult] = []
        for r in results:
            if isinstance(r, Exception):
                print(f"Evaluation error: {r}")
                continue
            clean_results.append(r)

        self.results.extend(clean_results)
        return clean_results

    async def _evaluate_single(
        self, agent: CodingAgent, task: BenchmarkTask
    ) -> EvaluationResult:
        """Execute and evaluate a single task."""
        # Run the agent
        output = await agent.execute(task)
        output.end_time = datetime.utcnow()

        # Store the output for later analysis
        await self._store_agent_output(output)

        # Evaluate
        evaluator = self._evaluators.get(task.task_type.name)
        if evaluator:
            result = await evaluator.evaluate(task, output)
        else:
            # Default: basic pass/fall based on human verification if available
            result = EvaluationResult(
                task_id=task.task_id,
                agent_name=agent.name,
                model_id=agent.model_id,
                passed=output.human_correct or False,
                score=1.0 if output.human_correct else 0.0,
            )

        return result

    async def _store_agent_output(self, output: AgentOutput) -> None:
        """Persist agent output to disk for later analysis."""
        out_dir = Path("data/outputs") / output.agent_name
        out_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{output.task_id.stable_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = out_dir / filename

        with open(filepath, "w") as f:
            json.dump(output.to_dict(), f, indent=2, default=str)

    def get_summary_stats(self) -> dict[str, Any]:
        """Generate summary statistics across all results."""
        if not self.results:
            return {}

        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        scores = [r.score for r in self.results]

        # By task type
        by_type: dict[str, dict[str, Any]] = {}
        for r in self.results:
            task = self.tasks.get(str(r.task_id))
            if task:
                tname = task.task_type.name
                if tname not in by_type:
                    by_type[tname] = {"total": 0, "passed": 0, "scores": []}
                by_type[tname]["total"] += 1
                if r.passed:
                    by_type[tname]["passed"] += 1
                by_type[tname]["scores"].append(r.score)

        for tname in by_type:
            scores_list = by_type[tname]["scores"]
            by_type[tname]["pass_rate"] = by_type[tname]["passed"] / max(by_type[tname]["total"], 1)
            by_type[tname]["avg_score"] = float(np.mean(scores_list)) if scores_list else 0.0

        return {
            "total_tasks": total,
            "passed": passed,
            "pass_rate": passed / total,
            "avg_score": float(np.mean(scores)),
            "median_score": float(np.median(scores)),
            "std_score": float(np.std(scores)),
            "by_task_type": by_type,
        }

    def save(self, path: str) -> None:
        """Save the benchmark suite to disk."""
        data = {
            "name": self.name,
            "description": self.description,
            "tasks": {k: v.to_dict() for k, v in self.tasks.items()},
            "results": [r.to_dict() for r in self.results],
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    @classmethod
    def load(cls, path: str) -> BenchmarkSuite:
        """Load a benchmark suite from disk."""
        with open(path) as f:
            data = json.load(f)

        suite = cls(name=data["name"], description=data.get("description", ""))
        for task_data in data.get("tasks", {}).values():
            suite.add_task(BenchmarkTask.from_dict(task_data))

        suite.results = [
            EvaluationResult.from_dict(result_data)
            for result_data in data.get("results", [])
        ]
        return suite
