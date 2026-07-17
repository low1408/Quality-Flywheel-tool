"""Prompt candidates, optimization state, and evaluator protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

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
            "evaluation_results": self.evaluation_results,
            "pass_rate": self.pass_rate,
            "avg_score": self.avg_score,
            "cost_per_task": self.cost_per_task,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromptCandidate:
        """Reconstruct a candidate without dropping its evaluation history."""
        return cls(
            prompt_id=str(data["prompt_id"]),
            system_prompt=str(data["system_prompt"]),
            few_shot_examples=list(data.get("few_shot_examples", [])),
            generation=int(data.get("generation", 0)),
            parent_ids=list(data.get("parent_ids", [])),
            mutation_type=data.get("mutation_type"),
            fitness_score=float(data.get("fitness_score", 0.0)),
            evaluation_results=list(data.get("evaluation_results", [])),
            pass_rate=float(data.get("pass_rate", 0.0)),
            avg_score=float(data.get("avg_score", 0.0)),
            cost_per_task=float(data.get("cost_per_task", 0.0)),
        )


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

    def to_dict(self) -> dict[str, Any]:
        """Serialize the restartable optimizer state."""
        return {
            "generation": self.generation,
            "population": [candidate.to_dict() for candidate in self.population],
            "best_candidate": self.best_candidate.to_dict() if self.best_candidate else None,
            "best_fitness_history": self.best_fitness_history,
            "avg_fitness_history": self.avg_fitness_history,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OptimizationState:
        """Reconstruct optimizer state from disk."""
        best_candidate = data.get("best_candidate")
        return cls(
            generation=int(data.get("generation", 0)),
            population=[
                PromptCandidate.from_dict(candidate)
                for candidate in data.get("population", [])
            ],
            best_candidate=(
                PromptCandidate.from_dict(best_candidate)
                if isinstance(best_candidate, dict)
                else None
            ),
            best_fitness_history=list(data.get("best_fitness_history", [])),
            avg_fitness_history=list(data.get("avg_fitness_history", [])),
        )


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
