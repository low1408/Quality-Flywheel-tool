"""Coding-agent interface consumed by benchmark and optimization services."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .benchmark_models import AgentOutput, BenchmarkTask

# -----------------------------------------------------------------------------
# Agent Interface
# -----------------------------------------------------------------------------

class CodingAgent(ABC):
    """
    Abstract interface for coding agents to be evaluated.

    Implement this for each agent you want to benchmark:
    - Claude Code
    - Codex CLI
    - OpenHands
    - Custom agents
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable agent identifier."""
        pass

    @property
    @abstractmethod
    def model_id(self) -> str | None:
        """The underlying LLM model, if applicable."""
        pass

    @abstractmethod
    async def execute(self, task: BenchmarkTask) -> AgentOutput:
        """
        Execute the agent on a benchmark task and return structured output.

        Implementations MUST capture:
        - All tool calls with arguments and results
        - The full execution trajectory
        - Token usage and cost
        - Final code/output
        """
        pass

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the current system prompt for this agent."""
        pass

    @abstractmethod
    def update_system_prompt(self, new_prompt: str) -> None:
        """Update the system prompt (for prompt optimization experiments)."""
        pass

