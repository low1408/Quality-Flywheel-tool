"""Benchmark-backed prompt candidate evaluation."""

from __future__ import annotations

from typing import Any, Callable

from .models import PromptCandidate

class BenchmarkPromptEvaluator:
    """
    Evaluates prompts by running them against a benchmark suite.

    This connects the prompt optimizer to the benchmark framework.
    """

    def __init__(
        self,
        benchmark_suite: Any,  # BenchmarkSuite from core.benchmark
        agent_factory: Callable[[str], Any],  # Creates agent with given system prompt
        num_tasks: int | None = None,  # Subset for faster evaluation
    ):
        self.benchmark_suite = benchmark_suite
        self.agent_factory = agent_factory
        self.num_tasks = num_tasks

    async def evaluate(self, candidate: PromptCandidate) -> dict[str, Any]:
        """Evaluate a prompt candidate against the benchmark."""
        # Create agent with this prompt
        agent = self.agent_factory(candidate.system_prompt)

        # Run evaluation on subset if specified
        task_filter = None
        if self.num_tasks:
            all_tasks = list(self.benchmark_suite.tasks.values())
            selected_tasks = all_tasks[:self.num_tasks]
            task_ids = {str(t.task_id) for t in selected_tasks}
            task_filter = lambda t: str(t.task_id) in task_ids

        # Run benchmark
        results = await self.benchmark_suite.run_evaluation(
            agent=agent,
            task_filter=task_filter,
            max_concurrent=4,
        )

        # Calculate metrics
        if results:
            passed = sum(1 for r in results if r.passed)
            scores = [r.score for r in results]
            total_cost = sum(
                r.test_results[0].execution_time_ms if r.test_results else 0
                for r in results
            )  # Placeholder for actual cost

            return {
                "pass_rate": passed / len(results),
                "avg_score": sum(scores) / len(scores),
                "cost_per_task": total_cost / len(results),
                "per_task_results": [r.to_dict() for r in results],
            }
        else:
            return {
                "pass_rate": 0.0,
                "avg_score": 0.0,
                "cost_per_task": 0.0,
                "per_task_results": [],
            }

