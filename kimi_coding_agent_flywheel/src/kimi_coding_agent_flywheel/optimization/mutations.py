"""Mutation and crossover strategies for prompt candidates."""

from __future__ import annotations

import copy
import random
import re
from abc import ABC, abstractmethod
from typing import Callable

from .models import PromptCandidate

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

