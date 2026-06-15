"""
Demo script showing the complete quality flywheel in action.

This demonstrates:
1. Creating a benchmark suite with various task types
2. Running agents through the flywheel
3. Collecting outputs and user feedback
4. Diagnosing failures and clustering
5. Optimizing prompts
6. Running regression tests
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.benchmark import (
    BenchmarkSuite,
    BenchmarkTask,
    Difficulty,
    TaskId,
    TaskType,
    TestCase,
)
from core.flywheel import FlywheelConfig, QualityFlywheel
from examples.example_agent_wrappers import MockCodingAgent


def create_demo_benchmark_suite() -> BenchmarkSuite:
    """Create a demo benchmark suite with representative tasks."""
    suite = BenchmarkSuite(
        name="coding-agent-eval-v1",
        description="Evaluation suite for coding agent quality flywheel",
    )

    # Task 1: Simple function generation (HumanEval-style)
    suite.add_task(BenchmarkTask(
        task_id=TaskId(namespace="demo", name="has-close-elements"),
        task_type=TaskType.CODE_GENERATION,
        difficulty=Difficulty.EASY,
        instruction="""Write a function that checks if any two numbers in a list are closer to each other than a given threshold.

Function signature: def has_close_elements(numbers: List[float], threshold: float) -> bool

Example:
- has_close_elements([1.0, 2.0, 3.0], 0.5) -> False
- has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3) -> True""",
        test_cases=[
            TestCase(
                name="basic_false",
                test_type="unit",
                expected_behavior="Returns False when no elements are close",
                weight=0.2,
            ),
            TestCase(
                name="basic_true",
                test_type="unit",
                expected_behavior="Returns True when elements are close",
                weight=0.2,
            ),
            TestCase(
                name="empty_list",
                test_type="unit",
                expected_behavior="Handles empty list",
                weight=0.2,
            ),
            TestCase(
                name="single_element",
                test_type="unit",
                expected_behavior="Handles single element",
                weight=0.2,
            ),
            TestCase(
                name="negative_numbers",
                test_type="unit",
                expected_behavior="Works with negative numbers",
                weight=0.2,
            ),
        ],
        tags=["function-generation", "list-processing", "easy"],
        language="python",
    ))

    # Task 2: Bug fixing (SWE-bench style)
    suite.add_task(BenchmarkTask(
        task_id=TaskId(namespace="demo", name="fix-parse-date"),
        task_type=TaskType.BUG_FIXING,
        difficulty=Difficulty.MEDIUM,
        instruction="""Fix the bug in the parse_date function. The current implementation fails when given dates in YYYY-MM-DD format.

Current (buggy) code:
```python
def parse_date(date_str):
    parts = date_str.split('/')
    return datetime(int(parts[2]), int(parts[0]), int(parts[1]))
```

The function should handle both MM/DD/YYYY and YYYY-MM-DD formats.""",
        test_cases=[
            TestCase(
                name="slash_format",
                test_type="unit",
                expected_behavior="Parses MM/DD/YYYY correctly",
                weight=0.3,
            ),
            TestCase(
                name="dash_format",
                test_type="unit",
                expected_behavior="Parses YYYY-MM-DD correctly",
                weight=0.3,
            ),
            TestCase(
                name="invalid_format",
                test_type="unit",
                expected_behavior="Raises ValueError for invalid format",
                weight=0.2,
            ),
            TestCase(
                name="edge_cases",
                test_type="unit",
                expected_behavior="Handles edge cases like leap years",
                weight=0.2,
            ),
        ],
        tags=["bug-fixing", "date-parsing", "medium"],
        language="python",
    ))

    # Task 3: Refactoring
    suite.add_task(BenchmarkTask(
        task_id=TaskId(namespace="demo", name="refactor-nested-loops"),
        task_type=TaskType.REFACTORING,
        difficulty=Difficulty.MEDIUM,
        instruction="""Refactor the following code to use list comprehensions and improve readability:

```python
def process_data(data):
    result = []
    for item in data:
        if item['active']:
            temp = []
            for value in item['values']:
                if value > 0:
                    temp.append(value * 2)
            result.append({'id': item['id'], 'processed': temp})
    return result
```

Maintain the same functionality but make it more Pythonic and efficient.""",
        test_cases=[
            TestCase(
                name="functionality_preserved",
                test_type="unit",
                expected_behavior="Refactored code produces identical output",
                weight=0.5,
            ),
            TestCase(
                name="uses_comprehensions",
                test_type="behavioral",
                expected_behavior="Uses list/dict comprehensions",
                weight=0.3,
            ),
            TestCase(
                name="handles_empty_input",
                test_type="unit",
                expected_behavior="Handles empty input correctly",
                weight=0.2,
            ),
        ],
        tags=["refactoring", "comprehensions", "medium"],
        language="python",
    ))

    # Task 4: Test generation
    suite.add_task(BenchmarkTask(
        task_id=TaskId(namespace="demo", name="generate-tests-calc"),
        task_type=TaskType.TEST_GENERATION,
        difficulty=Difficulty.MEDIUM,
        instruction="""Write comprehensive unit tests for the following Calculator class:

```python
class Calculator:
    def add(self, a, b):
        return a + b

    def divide(self, a, b):
        return a / b

    def factorial(self, n):
        if n <= 1:
            return 1
        return n * self.factorial(n - 1)
```

Include tests for:
- Normal cases
- Edge cases (zero, negative numbers)
- Error cases (division by zero)
- Performance (factorial of large number)""",
        test_cases=[
            TestCase(
                name="covers_add",
                test_type="behavioral",
                expected_behavior="Tests cover add method",
                weight=0.2,
            ),
            TestCase(
                name="covers_divide",
                test_type="behavioral",
                expected_behavior="Tests cover divide method including division by zero",
                weight=0.3,
            ),
            TestCase(
                name="covers_factorial",
                test_type="behavioral",
                expected_behavior="Tests cover factorial including edge cases",
                weight=0.3,
            ),
            TestCase(
                name="uses_proper_framework",
                test_type="behavioral",
                expected_behavior="Uses pytest or unittest properly",
                weight=0.2,
            ),
        ],
        tags=["test-generation", "unit-tests", "medium"],
        language="python",
    ))

    # Task 5: Debugging runtime error
    suite.add_task(BenchmarkTask(
        task_id=TaskId(namespace="demo", name="debug-keyerror"),
        task_type=TaskType.DEBUGGING,
        difficulty=Difficulty.EASY,
        instruction="""Fix the following code that raises a KeyError:

```python
def get_user_email(user_id, user_db):
    return user_db[user_id]['email']
```

The function should:
1. Handle the case where user_id doesn't exist (return None)
2. Handle the case where the user exists but has no 'email' key (return None)
3. Log a warning when user is not found""",
        test_cases=[
            TestCase(
                name="existing_user",
                test_type="unit",
                expected_behavior="Returns email for existing user",
                weight=0.3,
            ),
            TestCase(
                name="missing_user",
                test_type="unit",
                expected_behavior="Returns None for missing user without error",
                weight=0.4,
            ),
            TestCase(
                name="no_email_key",
                test_type="unit",
                expected_behavior="Returns None when email key missing",
                weight=0.3,
            ),
        ],
        tags=["debugging", "error-handling", "easy"],
        language="python",
    ))

    # Task 6: Terminal workflow (Terminal-Bench style)
    suite.add_task(BenchmarkTask(
        task_id=TaskId(namespace="demo", name="git-rebase-workflow"),
        task_type=TaskType.TERMINAL_WORKFLOW,
        difficulty=Difficulty.HARD,
        instruction="""Complete the following git workflow:

1. Clone the repository at https://github.com/example/repo.git
2. Create a feature branch called 'feature-auth' from main
3. Make a commit that adds authentication
4. Rebase onto main to incorporate recent changes
5. Resolve any merge conflicts
6. Push the branch to origin

Provide the complete sequence of git commands needed.""",
        test_cases=[
            TestCase(
                name="correct_sequence",
                test_type="behavioral",
                expected_behavior="Provides correct git command sequence",
                weight=0.4,
            ),
            TestCase(
                name="handles_conflicts",
                test_type="behavioral",
                expected_behavior="Includes conflict resolution steps",
                weight=0.3,
            ),
            TestCase(
                name="best_practices",
                test_type="behavioral",
                expected_behavior="Follows git best practices",
                weight=0.3,
            ),
        ],
        tags=["terminal", "git", "workflow", "hard"],
        language="bash",
    ))

    return suite


async def main():
    """Run the quality flywheel demonstration."""
    print("=" * 70)
    print("  CODING AGENT QUALITY FLYWHEEL - DEMONSTRATION")
    print("=" * 70)

    # Create benchmark suite
    print("\n[1] Creating benchmark suite...")
    suite = create_demo_benchmark_suite()
    print(f"    Created {len(suite.tasks)} benchmark tasks")
    for task in suite.tasks.values():
        print(f"      - {task.task_id} ({task.task_type.name}, {task.difficulty.name})")

    # Save suite
    suite.save("data/benchmarks/demo_suite.json")

    # Create agents with different success rates to demonstrate improvement
    print("\n[2] Creating test agents...")
    agent_v1 = MockCodingAgent(
        name="agent-v1",
        model_id="mock-1.0",
        system_prompt="You are a coding assistant. Write code to solve the given task.",
        simulate_success_rate=0.5,  # 50% success rate
    )
    print(f"    Agent v1: 50% simulated success rate")

    # Initialize flywheel
    print("\n[3] Initializing quality flywheel...")
    config = FlywheelConfig(
        benchmark_suite_path="data/benchmarks/demo_suite.json",
        genetic_generations=3,
        genetic_population=6,
        output_dir="data/flywheel_demo",
    )

    flywheel = QualityFlywheel(config)
    await flywheel.initialize()

    # Run first iteration with baseline agent
    print("\n[4] Running flywheel iteration 1 (baseline)...")
    results_1 = await flywheel.run_iteration(agent_v1)
    print(f"\n    Iteration 1 results:")
    print(f"      Pass rate: {results_1['evaluation']['pass_rate']:.3f}")
    print(f"      Failures found: {results_1['diagnosis'].get('failures_found', 0)}")

    # Create improved agent (simulating prompt optimization effect)
    print("\n[5] Simulating prompt improvement...")
    agent_v2 = MockCodingAgent(
        name="agent-v2",
        model_id="mock-1.1",
        system_prompt="""You are an expert software engineering agent.

Follow this structured approach:
1. Carefully analyze the requirements
2. Plan your solution before coding
3. Write clean, well-tested code
4. Handle all edge cases and errors
5. Validate your solution works correctly

Always test your code and fix any issues before submitting.""",
        simulate_success_rate=0.75,  # Improved to 75%
    )
    print(f"    Agent v2: 75% simulated success rate (after optimization)")

    # Run second iteration with improved agent
    print("\n[6] Running flywheel iteration 2 (improved)...")
    results_2 = await flywheel.run_iteration(agent_v2)
    print(f"\n    Iteration 2 results:")
    print(f"      Pass rate: {results_2['evaluation']['pass_rate']:.3f}")
    print(f"      Failures found: {results_2['diagnosis'].get('failures_found', 0)}")

    # Show flywheel status
    print("\n[7] Flywheel status:")
    status = flywheel.get_status()
    print(f"    Total iterations: {status['iteration']}")
    print(f"    Pass rate history: {[f'{p:.2f}' for p in status['pass_rate_history']]}")
    print(f"    Score history: {[f'{s:.2f}' for s in status['score_history']]}")
    print(f"    Known failure clusters: {status['known_failure_clusters']}")
    print(f"    Regression tests: {status['regression_tests']}")

    # Show final report
    print("\n[8] Generating comprehensive report...")
    report = flywheel.generate_report()
    print(f"    Report saved to: data/flywheel_demo/")

    print("\n" + "=" * 70)
    print("  DEMONSTRATION COMPLETE")
    print("=" * 70)
    print("""
The quality flywheel has completed 2 iterations. In a real deployment:

1. Benchmark Suite: Would contain 100s of tasks across multiple repos
2. Agent Execution: Would use real Codex/Claude Code/OpenHands APIs
3. Telemetry: Would capture every tool call, LLM interaction, and error
4. Failure Analysis: Would use LLM-as-judge for real diagnosis
5. Clustering: Would use embedding-based semantic clustering
6. Optimization: Would run genetic algorithms on real prompt variations
7. Regression: Would maintain and verify against growing test suite
8. Monitoring: Would track production traffic for new failure modes

Each iteration makes the agent more robust by:
- Identifying new failure patterns
- Optimizing prompts to prevent them
- Adding regression tests to catch recurrences
- Monitoring production for drift
""")


if __name__ == "__main__":
    asyncio.run(main())
