"""
Prompt Optimization Engine for Coding Agent Quality Flywheel.

Implements multiple optimization strategies:
1. Genetic Algorithm with mutation and crossover (inspired by GAAPO, EvoPrompt)
2. Error-driven optimization (inspired by APO/ProTeGi)
3. DSPy-style programmatic optimization
4. Few-shot example optimization

All strategies share a common evaluation framework and can be composed.
"""

from __future__ import annotations

import copy
import json
import random
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np


# -----------------------------------------------------------------------------
# Core Data Structures
# -----------------------------------------------------------------------------

@dataclass
class PromptCandidate:
    """A single prompt variant being evaluated."""
    prompt_id: str
    system_prompt: str
    few_shot_examples: list[dict[str, str]] = field(default_factory=list)

    # Metadata
    generation: int = 0
    parent_ids: list[str] = field(default_factory=list)
    mutation_type: str | None = None

    # Evaluation results
    fitness_score: float = 0.0
    evaluation_results: list[dict[str, Any]] = field(default_factory=list)
    pass_rate: float = 0.0
    avg_score: float = 0.0
    cost_per_task: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_id": self.prompt_id,
            "system_prompt": self.system_prompt,
            "few_shot_examples": self.few_shot_examples,
            "generation": self.generation,
            "parent_ids": self.parent_ids,
            "mutation_type": self.mutation_type,
            "fitness_score": self.fitness_score,
            "pass_rate": self.pass_rate,
            "avg_score": self.avg_score,
            "cost_per_task": self.cost_per_task,
        }


@dataclass
class OptimizationState:
    """Tracks the state of an ongoing optimization run."""
    generation: int = 0
    population: list[PromptCandidate] = field(default_factory=list)
    best_candidate: PromptCandidate | None = None
    best_fitness_history: list[float] = field(default_factory=list)
    avg_fitness_history: list[float] = field(default_factory=list)

    def update_best(self) -> None:
        if self.population:
            current_best = max(self.population, key=lambda p: p.fitness_score)
            if self.best_candidate is None or current_best.fitness_score > self.best_candidate.fitness_score:
                self.best_candidate = current_best

    def record_generation_stats(self) -> None:
        if self.population:
            scores = [p.fitness_score for p in self.population]
            self.best_fitness_history.append(max(scores))
            self.avg_fitness_history.append(sum(scores) / len(scores))


# -----------------------------------------------------------------------------
# Evaluation Interface
# -----------------------------------------------------------------------------

class PromptEvaluator(Protocol):
    """Protocol for evaluating prompt candidates."""

    async def evaluate(self, candidate: PromptCandidate) -> dict[str, Any]:
        """
        Evaluate a prompt candidate and return metrics.

        Returns dict with:
        - pass_rate: float (0-1)
        - avg_score: float (0-1)
        - cost_per_task: float (USD)
        - per_task_results: list of dicts
        """
        ...


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


# -----------------------------------------------------------------------------
# Prompt Generation Strategies
# -----------------------------------------------------------------------------

class MutationStrategy(ABC):
    """Abstract base for prompt mutation strategies."""

    @abstractmethod
    def mutate(self, parent: PromptCandidate, generation: int) -> PromptCandidate:
        """Create a new candidate by mutating the parent."""
        pass

    @abstractmethod
    def name(self) -> str:
        pass


class InstructionExpansionMutation(MutationStrategy):
    """Add more detailed guidelines to the prompt."""

    EXPANSION_TEMPLATES = [
        "\n\nBe thorough and check your work carefully.",
        "\n\nAlways validate your solution by running tests before completing.",
        "\n\nIf you encounter errors, debug step by step and fix the root cause.",
        "\n\nBreak complex tasks into smaller steps and tackle each one systematically.",
        "\n\nConsider edge cases and handle errors gracefully in your code.",
        "\n\nAfter making changes, verify that existing functionality still works.",
    ]

    def mutate(self, parent: PromptCandidate, generation: int) -> PromptCandidate:
        addition = random.choice(self.EXPANSION_TEMPLATES)
        new_prompt = parent.system_prompt + addition

        return PromptCandidate(
            prompt_id=f"mut_exp_{generation}_{random.randint(1000, 9999)}",
            system_prompt=new_prompt,
            few_shot_examples=copy.deepcopy(parent.few_shot_examples),
            generation=generation,
            parent_ids=[parent.prompt_id],
            mutation_type="instruction_expansion",
        )

    def name(self) -> str:
        return "instruction_expansion"


class ConstraintAdditionMutation(MutationStrategy):
    """Add specific constraints to the prompt."""

    CONSTRAINT_TEMPLATES = [
        "\n\nIMPORTANT: Never use deprecated APIs. Always check documentation.",
        "\n\nIMPORTANT: Write type hints for all function signatures.",
        "\n\nIMPORTANT: Include docstrings for all public functions.",
        "\n\nIMPORTANT: Handle all exceptions with appropriate error messages.",
        "\n\nIMPORTANT: Write unit tests for any new functions you create.",
        "\n\nIMPORTANT: Follow the existing code style in the repository.",
    ]

    def mutate(self, parent: PromptCandidate, generation: int) -> PromptCandidate:
        constraint = random.choice(self.CONSTRAINT_TEMPLATES)
        new_prompt = parent.system_prompt + constraint

        return PromptCandidate(
            prompt_id=f"mut_con_{generation}_{random.randint(1000, 9999)}",
            system_prompt=new_prompt,
            few_shot_examples=copy.deepcopy(parent.few_shot_examples),
            generation=generation,
            parent_ids=[parent.prompt_id],
            mutation_type="constraint_addition",
        )

    def name(self) -> str:
        return "constraint_addition"


class RoleAssignmentMutation(MutationStrategy):
    """Modify the role/persona in the prompt."""

    ROLE_TEMPLATES = [
        "You are an expert software engineer with 20 years of experience.",
        "You are a meticulous code reviewer who catches every bug.",
        "You are a senior developer who writes production-quality code.",
        "You are a defensive programmer who always validates inputs.",
        "You are a test-driven developer who writes tests before implementation.",
    ]

    def mutate(self, parent: PromptCandidate, generation: int) -> PromptCandidate:
        new_role = random.choice(self.ROLE_TEMPLATES)

        # Replace or prepend role
        lines = parent.system_prompt.split("\n")
        if lines and ("you are" in lines[0].lower() or "act as" in lines[0].lower()):
            lines[0] = new_role
            new_prompt = "\n".join(lines)
        else:
            new_prompt = new_role + "\n\n" + parent.system_prompt

        return PromptCandidate(
            prompt_id=f"mut_role_{generation}_{random.randint(1000, 9999)}",
            system_prompt=new_prompt,
            few_shot_examples=copy.deepcopy(parent.few_shot_examples),
            generation=generation,
            parent_ids=[parent.prompt_id],
            mutation_type="role_assignment",
        )

    def name(self) -> str:
        return "role_assignment"


class TaskDecompositionMutation(MutationStrategy):
    """Add step-by-step decomposition instructions."""

    def mutate(self, parent: PromptCandidate, generation: int) -> PromptCandidate:
        decomposition = """

Follow this structured approach:
1. Analyze the requirements and identify constraints
2. Plan your solution before writing code
3. Implement the solution step by step
4. Test your solution with example inputs
5. Verify edge cases are handled
6. Review and refactor if needed"""

        new_prompt = parent.system_prompt + decomposition

        return PromptCandidate(
            prompt_id=f"mut_decomp_{generation}_{random.randint(1000, 9999)}",
            system_prompt=new_prompt,
            few_shot_examples=copy.deepcopy(parent.few_shot_examples),
            generation=generation,
            parent_ids=[parent.prompt_id],
            mutation_type="task_decomposition",
        )

    def name(self) -> str:
        return "task_decomposition"


class FewShotExampleMutation(MutationStrategy):
    """Add or modify few-shot examples."""

    def __init__(self, example_pool: list[dict[str, str]] | None = None):
        self.example_pool = example_pool or []

    def mutate(self, parent: PromptCandidate, generation: int) -> PromptCandidate:
        new_examples = copy.deepcopy(parent.few_shot_examples)

        if self.example_pool:
            # Add a new example from the pool
            new_example = random.choice(self.example_pool)
            new_examples.append(new_example)
        else:
            # Create a synthetic example placeholder
            new_examples.append({
                "task": "Example task description",
                "solution": "Example solution approach",
            })

        return PromptCandidate(
            prompt_id=f"mut_fs_{generation}_{random.randint(1000, 9999)}",
            system_prompt=parent.system_prompt,
            few_shot_examples=new_examples,
            generation=generation,
            parent_ids=[parent.prompt_id],
            mutation_type="few_shot_addition",
        )

    def name(self) -> str:
        return "few_shot_addition"


class ConciseOptimizationMutation(MutationStrategy):
    """Remove redundant content to make prompt more concise."""

    def mutate(self, parent: PromptCandidate, generation: int) -> PromptCandidate:
        # Simple heuristic: remove redundant sentences
        lines = parent.system_prompt.split("\n")
        filtered_lines = []
        seen = set()

        for line in lines:
            normalized = re.sub(r'\s+', ' ', line.strip().lower())
            if normalized and normalized not in seen:
                seen.add(normalized)
                filtered_lines.append(line)

        new_prompt = "\n".join(filtered_lines)

        return PromptCandidate(
            prompt_id=f"mut_concise_{generation}_{random.randint(1000, 9999)}",
            system_prompt=new_prompt,
            few_shot_examples=copy.deepcopy(parent.few_shot_examples),
            generation=generation,
            parent_ids=[parent.prompt_id],
            mutation_type="concise_optimization",
        )

    def name(self) -> str:
        return "concise_optimization"


class LLMBasedMutation(MutationStrategy):
    """
    Use an LLM to generate intelligent prompt mutations.

    Inspired by GAAPO and EvoPrompt - use LLM for semantic mutations.
    """

    MUTATION_PROMPT_TEMPLATE = """You are an expert prompt engineer.

Your task is to improve the following system prompt for a coding agent.

CURRENT PROMPT:
```
{current_prompt}
```

PERFORMANCE ISSUE: {failure_description}

Create an improved version of this prompt that addresses the issue.
Only return the improved prompt, nothing else.
"""

    def __init__(self, llm_fn: Callable[[str], str] | None = None):
        self.llm_fn = llm_fn

    def mutate(self, parent: PromptCandidate, generation: int) -> PromptCandidate:
        if not self.llm_fn:
            # Fallback to identity mutation
            return PromptCandidate(
                prompt_id=f"mut_llm_{generation}_{random.randint(1000, 9999)}",
                system_prompt=parent.system_prompt,
                few_shot_examples=copy.deepcopy(parent.few_shot_examples),
                generation=generation,
                parent_ids=[parent.prompt_id],
                mutation_type="llm_based",
            )

        # Use failure information if available
        failure_desc = "General improvement needed"
        if parent.evaluation_results:
            # Extract failure info from evaluation
            failure_desc = "Agent struggles with tool selection and error handling"

        prompt = self.MUTATION_PROMPT_TEMPLATE.format(
            current_prompt=parent.system_prompt,
            failure_description=failure_desc,
        )

        try:
            new_prompt = self.llm_fn(prompt)
            # Clean up the response
            new_prompt = new_prompt.strip()
            if new_prompt.startswith("```"):
                new_prompt = new_prompt.split("```")[1] if "```" in new_prompt[3:] else new_prompt
        except Exception:
            new_prompt = parent.system_prompt

        return PromptCandidate(
            prompt_id=f"mut_llm_{generation}_{random.randint(1000, 9999)}",
            system_prompt=new_prompt,
            few_shot_examples=copy.deepcopy(parent.few_shot_examples),
            generation=generation,
            parent_ids=[parent.prompt_id],
            mutation_type="llm_based",
        )

    def name(self) -> str:
        return "llm_based"


class CrossoverStrategy:
    """Combine two parent prompts to create offspring."""

    def crossover(self, parent1: PromptCandidate, parent2: PromptCandidate, generation: int) -> PromptCandidate:
        """
        Create a new prompt by combining parts of two parents.

        Strategy: Split each prompt at midpoint and swap halves.
        """
        p1_lines = parent1.system_prompt.split("\n")
        p2_lines = parent2.system_prompt.split("\n")

        mid1 = len(p1_lines) // 2
        mid2 = len(p2_lines) // 2

        # Combine first half of parent1 with second half of parent2
        combined_lines = p1_lines[:mid1] + p2_lines[mid2:]
        new_prompt = "\n".join(combined_lines)

        # Combine few-shot examples
        new_examples = parent1.few_shot_examples[:1] + parent2.few_shot_examples[:1]

        return PromptCandidate(
            prompt_id=f"cross_{generation}_{random.randint(1000, 9999)}",
            system_prompt=new_prompt,
            few_shot_examples=new_examples,
            generation=generation,
            parent_ids=[parent1.prompt_id, parent2.prompt_id],
            mutation_type="crossover",
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
        state = self.get_optimization_history()
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(state, f, indent=2, default=str)


# -----------------------------------------------------------------------------
# Error-Driven Prompt Optimizer (APO/ProTeGi inspired)
# -----------------------------------------------------------------------------

class ErrorDrivenOptimizer:
    """
    Optimizes prompts by analyzing errors and generating targeted improvements.

    Inspired by APO (Automatic Prompt Optimizer) / ProTeGi:
    1. Identify errors from evaluation
    2. Group errors by type
    3. Generate "gradients" (improvement directions)
    4. Apply targeted prompt modifications
    """

    def __init__(
        self,
        evaluator: PromptEvaluator,
        llm_improver: Callable[[str], str] | None = None,
    ):
        self.evaluator = evaluator
        self.llm_improver = llm_improver
        self.error_history: list[dict[str, Any]] = []

    async def optimize(
        self,
        current_prompt: str,
        failure_clusters: list[Any],  # FailureCluster objects
        max_iterations: int = 5,
    ) -> str:
        """
        Iteratively improve prompt based on identified failure clusters.

        Args:
            current_prompt: The current system prompt
            failure_clusters: Clusters of failures from the analyzer
            max_iterations: Maximum optimization iterations

        Returns:
            Improved prompt
        """
        prompt = current_prompt

        for iteration in range(max_iterations):
            print(f"\n--- Error-Driven Optimization Iteration {iteration + 1} ---")

            # Generate improvement based on top failure clusters
            improvements = []
            for cluster in failure_clusters[:3]:  # Focus on top 3 clusters
                if hasattr(cluster, 'suggested_prompt_fix') and cluster.suggested_prompt_fix:
                    improvements.append(cluster.suggested_prompt_fix)

            if not improvements:
                print("  No specific improvements identified.")
                break

            # Apply improvements
            if self.llm_improver:
                prompt = self._apply_llm_improvements(prompt, improvements, failure_clusters)
            else:
                prompt = self._apply_heuristic_improvements(prompt, improvements)

            # Validate improvement
            candidate = PromptCandidate(
                prompt_id=f"apo_iter_{iteration}",
                system_prompt=prompt,
            )
            result = await self.evaluator.evaluate(candidate)

            print(f"  Pass rate after improvement: {result.get('pass_rate', 0):.3f}")

            # Store error history
            self.error_history.append({
                "iteration": iteration,
                "improvements": improvements,
                "result": result,
            })

            # Check if we've solved the failures
            if result.get("pass_rate", 0) > 0.95:
                print("  Achieved target pass rate!")
                break

        return prompt

    def _apply_llm_improvements(
        self,
        prompt: str,
        improvements: list[str],
        failure_clusters: list[Any],
    ) -> str:
        """Use LLM to intelligently apply improvements."""
        if not self.llm_improver:
            return self._apply_heuristic_improvements(prompt, improvements)

        improvement_text = "\n".join(f"- {imp}" for imp in improvements)
        cluster_descriptions = "\n".join(
            f"- {getattr(c, 'label', 'unknown')}: {getattr(c, 'description', '')}"
            for c in failure_clusters[:3]
        )

        prompt_template = f"""You are improving a system prompt for a coding agent.

CURRENT PROMPT:
```
{prompt}
```

IDENTIFIED ISSUES:
{cluster_descriptions}

SUGGESTED IMPROVEMENTS:
{improvement_text}

Please rewrite the system prompt to address these issues while keeping it clear and concise.
Only return the improved prompt, nothing else.
"""

        try:
            improved = self.llm_improver(prompt_template)
            return improved.strip()
        except Exception:
            return self._apply_heuristic_improvements(prompt, improvements)

    def _apply_heuristic_improvements(self, prompt: str, improvements: list[str]) -> str:
        """Apply improvements heuristically by appending guidance."""
        # Add an "IMPORTANT REMINDERS" section
        reminder_section = "\n\n## IMPORTANT REMINDERS\n\n"
        for imp in improvements:
            reminder_section += f"- {imp}\n"

        # Only add if not already present
        if "IMPORTANT REMINDERS" not in prompt:
            return prompt + reminder_section
        return prompt


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
