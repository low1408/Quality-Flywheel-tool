"""Backward-compatible facade for prompt optimization APIs."""

from .composite import CompositePromptOptimizer
from .error_driven import ErrorDrivenOptimizer
from .evaluation import BenchmarkPromptEvaluator
from .genetic import GeneticPromptOptimizer
from .models import OptimizationState, PromptCandidate, PromptEvaluator
from .mutations import (
    ConciseOptimizationMutation,
    ConstraintAdditionMutation,
    CrossoverStrategy,
    FewShotExampleMutation,
    InstructionExpansionMutation,
    LLMBasedMutation,
    MutationStrategy,
    RoleAssignmentMutation,
    TaskDecompositionMutation,
)

__all__ = [
    "BenchmarkPromptEvaluator",
    "CompositePromptOptimizer",
    "ConciseOptimizationMutation",
    "ConstraintAdditionMutation",
    "CrossoverStrategy",
    "ErrorDrivenOptimizer",
    "FewShotExampleMutation",
    "GeneticPromptOptimizer",
    "InstructionExpansionMutation",
    "LLMBasedMutation",
    "MutationStrategy",
    "OptimizationState",
    "PromptCandidate",
    "PromptEvaluator",
    "RoleAssignmentMutation",
    "TaskDecompositionMutation",
]
