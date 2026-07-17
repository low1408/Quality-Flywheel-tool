"""Factory helpers for common benchmark task formats."""

from __future__ import annotations

from .benchmark_models import BenchmarkTask, Difficulty, TaskId, TaskType, TestCase

# -----------------------------------------------------------------------------
# Factory Functions for Common Benchmark Patterns
# -----------------------------------------------------------------------------

def create_swe_bench_style_task(
    repo: str,
    issue_id: str,
    issue_description: str,
    base_commit: str,
    test_patch: str,
    gold_patch: str,
) -> BenchmarkTask:
    """
    Create a task in the style of SWE-bench.

    SWE-bench tasks are real GitHub issues with associated test patches.
    """
    return BenchmarkTask(
        task_id=TaskId(namespace=f"swe-bench-{repo}", name=issue_id),
        task_type=TaskType.BUG_FIXING,
        difficulty=Difficulty.MEDIUM,
        instruction=issue_description,
        context_files={
            "test_patch.py": test_patch,
            "expected_fix.patch": gold_patch,
        },
        setup_commands=[
            f"git checkout {base_commit}",
            f"git apply test_patch.py",
        ],
        test_cases=[
            TestCase(
                name="fail_to_pass",
                test_type="integration",
                expected_behavior="Patch resolves the issue and all tests pass",
                weight=1.0,
            ),
        ],
        tags=["swe-bench", repo, "github-issue"],
        language="python",
    )


def create_terminal_bench_style_task(
    task_name: str,
    description: str,
    setup_script: str,
    verification_commands: list[str],
    expected_outputs: list[str],
    difficulty: Difficulty = Difficulty.HARD,
) -> BenchmarkTask:
    """
    Create a task in the style of Terminal-Bench.

    Terminal-Bench tasks test multi-step terminal workflows.
    """
    return BenchmarkTask(
        task_id=TaskId(namespace="terminal-bench", name=task_name),
        task_type=TaskType.TERMINAL_WORKFLOW,
        difficulty=difficulty,
        instruction=description,
        setup_commands=[setup_script],
        test_cases=[
            TestCase(
                name=f"verify_step_{i}",
                test_type="integration",
                expected_behavior=f"Command '{cmd}' produces expected output",
                weight=1.0 / len(verification_commands),
            )
            for i, cmd in enumerate(verification_commands)
        ],
        tags=["terminal-bench", "cli", "workflow"],
        language="bash",
    )


def create_humaneval_style_task(
    task_id: str,
    prompt: str,
    canonical_solution: str,
    test_code: str,
    entry_point: str,
) -> BenchmarkTask:
    """
    Create a task in the style of HumanEval.

    HumanEval tasks test single-function generation from docstrings.
    """
    return BenchmarkTask(
        task_id=TaskId(namespace="humaneval", name=task_id),
        task_type=TaskType.CODE_GENERATION,
        difficulty=Difficulty.EASY,
        instruction=f"Complete the following function:\n\n{prompt}",
        context_files={
            "test.py": test_code,
            "solution.py": canonical_solution,
        },
        test_cases=[
            TestCase(
                name="functional_correctness",
                test_type="unit",
                expected_behavior=f"Function '{entry_point}' passes all test cases",
                weight=1.0,
            ),
        ],
        tags=["humaneval", "function-generation", "python"],
        language="python",
    )
