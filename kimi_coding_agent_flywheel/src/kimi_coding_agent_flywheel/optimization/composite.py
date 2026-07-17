"""Composition of genetic and error-driven prompt optimizers."""

from __future__ import annotations

from typing import Any

from .error_driven import ErrorDrivenOptimizer
from .genetic import GeneticPromptOptimizer
from .models import PromptCandidate

# -----------------------------------------------------------------------------
# Composite Optimizer (runs multiple strategies)
# -----------------------------------------------------------------------------

class CompositePromptOptimizer:
    """
    Runs multiple optimization strategies and selects the best result.

    Strategy:
    1. Run genetic algorithm for broad exploration
    2. Run error-driven optimization for targeted fixes
    3. Compare results and return best
    """

    def __init__(
        self,
        genetic_optimizer: GeneticPromptOptimizer | None = None,
        error_driven_optimizer: ErrorDrivenOptimizer | None = None,
    ):
        self.genetic = genetic_optimizer
        self.error_driven = error_driven_optimizer

    async def optimize(
        self,
        seed_prompt: str,
        failure_clusters: list[Any] | None = None,
        seed_examples: list[dict[str, str]] | None = None,
    ) -> PromptCandidate:
        """Run all optimization strategies and return the best result."""
        results = []

        # Genetic optimization
        if self.genetic:
            print("\n" + "=" * 50)
            print("RUNNING GENETIC OPTIMIZATION")
            print("=" * 50)
            genetic_result = await self.genetic.optimize(seed_prompt, seed_examples)
            results.append(("genetic", genetic_result))

        # Error-driven optimization
        if self.error_driven and failure_clusters:
            print("\n" + "=" * 50)
            print("RUNNING ERROR-DRIVEN OPTIMIZATION")
            print("=" * 50)
            start_prompt = results[0][1].system_prompt if results else seed_prompt
            improved_prompt = await self.error_driven.optimize(start_prompt, failure_clusters)

            # Evaluate the error-driven result
            candidate = PromptCandidate(
                prompt_id="error_driven_final",
                system_prompt=improved_prompt,
            )
            eval_result = await self.error_driven.evaluator.evaluate(candidate)
            self.genetic._update_candidate_fitness(candidate, eval_result)
            results.append(("error_driven", candidate))

        # Select best
        best = max(results, key=lambda r: r[1].fitness_score)
        print(f"\n{'=' * 50}")
        print(f"BEST RESULT: {best[0]} strategy")
        print(f"Fitness: {best[1].fitness_score:.3f}")
        print(f"Pass rate: {best[1].pass_rate:.3f}")

        return best[1]
