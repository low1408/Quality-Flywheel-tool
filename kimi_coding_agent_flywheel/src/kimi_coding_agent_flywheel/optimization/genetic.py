"""Genetic prompt optimizer."""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path
from typing import Any

from .models import OptimizationState, PromptCandidate, PromptEvaluator
from .mutations import (
    ConciseOptimizationMutation,
    ConstraintAdditionMutation,
    CrossoverStrategy,
    InstructionExpansionMutation,
    MutationStrategy,
    RoleAssignmentMutation,
    TaskDecompositionMutation,
)

# -----------------------------------------------------------------------------
# Genetic Algorithm Optimizer
# -----------------------------------------------------------------------------

class GeneticPromptOptimizer:
    """
    Genetic algorithm for prompt optimization.

    Inspired by GAAPO and EvoPrompt:
    - Population of prompt candidates
    - Fitness evaluation on benchmark
    - Selection of top performers
    - Crossover and mutation to generate new candidates
    - Iterative improvement over generations
    """

    DEFAULT_MUTATION_STRATEGIES = [
        InstructionExpansionMutation(),
        ConstraintAdditionMutation(),
        RoleAssignmentMutation(),
        TaskDecompositionMutation(),
        ConciseOptimizationMutation(),
    ]

    def __init__(
        self,
        evaluator: PromptEvaluator,
        mutation_strategies: list[MutationStrategy] | None = None,
        population_size: int = 20,
        num_generations: int = 10,
        top_k_selection: int = 5,
        mutation_rate: float = 0.7,
        crossover_rate: float = 0.3,
        elitism: int = 2,
        fitness_weights: dict[str, float] | None = None,
    ):
        """
        Args:
            evaluator: Function to evaluate prompt fitness
            mutation_strategies: List of mutation strategies to use
            population_size: Number of candidates per generation
            num_generations: Number of optimization iterations
            top_k_selection: Number of top candidates to select for breeding
            mutation_rate: Probability of applying mutation
            crossover_rate: Probability of applying crossover
            elitism: Number of top candidates to preserve unchanged
            fitness_weights: Weights for multi-objective fitness (pass_rate, score, cost)
        """
        self.evaluator = evaluator
        self.mutation_strategies = mutation_strategies or self.DEFAULT_MUTATION_STRATEGIES
        self.crossover_strategy = CrossoverStrategy()
        self.population_size = population_size
        self.num_generations = num_generations
        self.top_k = top_k_selection
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.elitism = elitism
        self.fitness_weights = fitness_weights or {"pass_rate": 0.5, "avg_score": 0.3, "inverse_cost": 0.2}

        self.state = OptimizationState()

    async def optimize(self, seed_prompt: str, seed_examples: list[dict[str, str]] | None = None) -> PromptCandidate:
        """
        Run genetic algorithm optimization starting from a seed prompt.

        Returns the best prompt candidate found.
        """
        # Initialize population with seed and mutations
        self.state.population = self._initialize_population(seed_prompt, seed_examples)

        for generation in range(self.num_generations):
            self.state.generation = generation
            print(f"\n--- Generation {generation + 1}/{self.num_generations} ---")

            # Evaluate all candidates
            await self._evaluate_population()

            # Record stats
            self.state.record_generation_stats()
            self.state.update_best()

            print(f"  Best fitness: {self.state.best_fitness_history[-1]:.3f}")
            print(f"  Avg fitness: {self.state.avg_fitness_history[-1]:.3f}")
            if self.state.best_candidate:
                print(f"  Best pass rate: {self.state.best_candidate.pass_rate:.3f}")

            # Check convergence
            if self._has_converged():
                print("  Converged early!")
                break

            # Create next generation
            if generation < self.num_generations - 1:
                self.state.population = self._create_next_generation(generation + 1)

        # Final evaluation of best
        if self.state.best_candidate:
            final_result = await self.evaluator.evaluate(self.state.best_candidate)
            self._update_candidate_fitness(self.state.best_candidate, final_result)

        return self.state.best_candidate or self.state.population[0]

    def _initialize_population(
        self,
        seed_prompt: str,
        seed_examples: list[dict[str, str]] | None = None,
    ) -> list[PromptCandidate]:
        """Create initial population with seed and variations."""
        population = []

        # Add seed
        population.append(PromptCandidate(
            prompt_id="seed_0",
            system_prompt=seed_prompt,
            few_shot_examples=seed_examples or [],
            generation=0,
        ))

        # Add mutations of seed
        for i in range(self.population_size - 1):
            strategy = random.choice(self.mutation_strategies)
            mutated = strategy.mutate(population[0], generation=0)
            mutated.prompt_id = f"gen0_{i}_{strategy.name()}"
            population.append(mutated)

        return population

    async def _evaluate_population(self) -> None:
        """Evaluate all candidates in the current population."""
        for candidate in self.state.population:
            if candidate.fitness_score == 0.0:  # Only evaluate if not already scored
                try:
                    result = await self.evaluator.evaluate(candidate)
                    self._update_candidate_fitness(candidate, result)
                except Exception as e:
                    print(f"  Evaluation failed for {candidate.prompt_id}: {e}")
                    candidate.fitness_score = 0.0

    def _update_candidate_fitness(self, candidate: PromptCandidate, result: dict[str, Any]) -> None:
        """Calculate composite fitness score from evaluation results."""
        candidate.pass_rate = result.get("pass_rate", 0.0)
        candidate.avg_score = result.get("avg_score", 0.0)
        candidate.cost_per_task = result.get("cost_per_task", 1.0)
        candidate.evaluation_results.append(result)

        # Calculate weighted fitness
        # Normalize cost: lower is better, so use inverse
        max_cost = 1.0  # Assumed max cost per task
        inverse_cost = max(0, 1.0 - (candidate.cost_per_task / max_cost))

        fitness = (
            self.fitness_weights["pass_rate"] * candidate.pass_rate +
            self.fitness_weights["avg_score"] * candidate.avg_score +
            self.fitness_weights["inverse_cost"] * inverse_cost
        )

        candidate.fitness_score = fitness

    def _create_next_generation(self, generation: int) -> list[PromptCandidate]:
        """Create the next generation through selection, crossover, and mutation."""
        # Sort by fitness
        sorted_pop = sorted(self.state.population, key=lambda p: p.fitness_score, reverse=True)

        new_population = []

        # Elitism: keep top candidates unchanged
        elites = sorted_pop[:self.elitism]
        for e in elites:
            new_population.append(PromptCandidate(
                prompt_id=f"elite_{e.prompt_id}",
                system_prompt=e.system_prompt,
                few_shot_examples=copy.deepcopy(e.few_shot_examples),
                generation=generation,
                parent_ids=[e.prompt_id],
                mutation_type="elitism",
                fitness_score=e.fitness_score,
                pass_rate=e.pass_rate,
                avg_score=e.avg_score,
            ))

        # Generate rest through crossover and mutation
        while len(new_population) < self.population_size:
            if random.random() < self.crossover_rate and len(sorted_pop) >= 2:
                # Crossover
                parents = random.sample(sorted_pop[:self.top_k], 2)
                child = self.crossover_strategy.crossover(parents[0], parents[1], generation)
                new_population.append(child)
            else:
                # Mutation
                parent = random.choice(sorted_pop[:self.top_k])
                strategy = random.choice(self.mutation_strategies)
                child = strategy.mutate(parent, generation)
                new_population.append(child)

        return new_population[:self.population_size]

    def _has_converged(self, patience: int = 3, threshold: float = 0.001) -> bool:
        """Check if optimization has converged."""
        if len(self.state.best_fitness_history) < patience + 1:
            return False

        recent = self.state.best_fitness_history[-patience:]
        return max(recent) - min(recent) < threshold

    def get_optimization_history(self) -> dict[str, Any]:
        """Return the full optimization history."""
        return {
            "generations": self.state.generation + 1,
            "best_fitness_history": self.state.best_fitness_history,
            "avg_fitness_history": self.state.avg_fitness_history,
            "final_best": self.state.best_candidate.to_dict() if self.state.best_candidate else None,
            "all_candidates": [p.to_dict() for p in self.state.population],
        }

    def save_state(self, filepath: str) -> None:
        """Save optimization state to disk."""
        state = {
            **self.get_optimization_history(),
            "schema_version": 1,
            "optimizer_state": self.state.to_dict(),
        }
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(state, f, indent=2, default=str)

    def load_state(self, filepath: str) -> None:
        """Restore optimizer state saved by :meth:`save_state`.

        Files written before the restartable state schema are accepted as a
        best-effort migration so existing optimization histories remain useful.
        """
        with open(filepath) as f:
            data = json.load(f)

        serialized_state = data.get("optimizer_state")
        if isinstance(serialized_state, dict):
            self.state = OptimizationState.from_dict(serialized_state)
            return

        final_best = data.get("final_best")
        self.state = OptimizationState(
            generation=max(int(data.get("generations", 1)) - 1, 0),
            population=[
                PromptCandidate.from_dict(candidate)
                for candidate in data.get("all_candidates", [])
            ],
            best_candidate=(
                PromptCandidate.from_dict(final_best)
                if isinstance(final_best, dict)
                else None
            ),
            best_fitness_history=list(data.get("best_fitness_history", [])),
            avg_fitness_history=list(data.get("avg_fitness_history", [])),
        )
