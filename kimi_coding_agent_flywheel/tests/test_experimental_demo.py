from __future__ import annotations

import pytest

from kimi_coding_agent_flywheel.examples.example_agent_wrappers import MockCodingAgent
from kimi_coding_agent_flywheel.examples.run_flywheel_demo import (
    _raise_for_iteration_errors,
)


def test_mock_agent_supports_flywheel_candidate_construction() -> None:
    candidate = MockCodingAgent(model="mock-2.0", system_prompt="Candidate prompt")

    assert candidate.model_id == "mock-2.0"
    assert candidate.get_system_prompt() == "Candidate prompt"


def test_demo_does_not_report_optimization_errors_as_success() -> None:
    with pytest.raises(RuntimeError, match="iteration 2 optimization failed: boom"):
        _raise_for_iteration_errors({"optimization": {"error": "boom"}}, 2)
