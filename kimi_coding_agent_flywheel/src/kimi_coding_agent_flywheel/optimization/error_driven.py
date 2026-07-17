"""Error-driven prompt optimization."""

from __future__ import annotations

from typing import Any, Callable

from .models import PromptCandidate, PromptEvaluator

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

