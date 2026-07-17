"""Failure taxonomy and normalized category metadata."""

from __future__ import annotations

# -----------------------------------------------------------------------------
# Failure Taxonomy (inspired by MAST - Multi-Agent System Failure Taxonomy)
# -----------------------------------------------------------------------------

class FailureCategory:
    """Hierarchical failure classification system."""

    # Specification & System Design Failures (~42% of failures)
    SPECIFICATION = "specification"
    SUBCATEGORY_DISOBEY_SPEC = "disobey_task_specification"
    SUBCATEGORY_MISSING_CONSTRAINT = "missing_role_constraint"
    SUBCATEGORY_REPETITION = "repeating_previous_steps"
    SUBCATEGORY_NO_TERMINATION = "failure_to_terminate"
    SUBCATEGORY_AMBIGUOUS_PROMPT = "ambiguous_prompt_interpretation"

    # Inter-Agent / Tool Misalignment (~37% of failures)
    TOOL_MISALIGNMENT = "tool_misalignment"
    SUBCATEGORY_WRONG_TOOL = "wrong_tool_selected"
    SUBCATEGORY_WRONG_ARGS = "incorrect_tool_arguments"
    SUBCATEGORY_TOOL_NOT_FOUND = "tool_not_found_or_unavailable"
    SUBCATEGORY_IGNORE_OUTPUT = "ignoring_tool_output"
    SUBCATEGORY_REPEATED_TOOL_ERRORS = "repeated_tool_errors"

    # Task Verification & Quality Control (~21% of failures)
    VERIFICATION = "verification"
    SUBCATEGORY_PREMATURE_STOP = "premature_task_termination"
    SUBCATEGORY_NO_VALIDATION = "skipping_validation"
    SUBCATEGORY_ACCEPTING_INCORRECT = "accepting_incorrect_solution"
    SUBCATEGORY_PARTIAL_SOLUTION = "partial_solution_accepted"

    # Code-Specific Failures
    CODE_SYNTAX = "code_syntax_error"
    CODE_LOGIC = "code_logic_error"
    CODE_RUNTIME = "code_runtime_error"
    CODE_IMPORT = "import_or_dependency_error"
    CODE_TYPE = "type_error"

    # Environment Failures
    ENV_SETUP = "environment_setup_failure"
    ENV_MISSING_DEP = "missing_dependency"
    ENV_PERMISSION = "permission_error"
    ENV_TIMEOUT = "execution_timeout"

    # LLM API Failures
    LLM_RATE_LIMIT = "rate_limit_exceeded"
    LLM_CONTEXT_WINDOW = "context_window_exceeded"
    LLM_REFUSAL = "model_refusal"
    LLM_HALLUCINATION = "model_hallucination"

    # Prompt Engineering Failures
    PROMPT_TOO_VAGUE = "prompt_too_vague"
    PROMPT_TOO_LONG = "prompt_context_overflow"
    PROMPT_FORMAT = "output_format_misunderstanding"


# Human-readable descriptions for each subcategory
FAILURE_DESCRIPTIONS: dict[str, str] = {
    FailureCategory.SUBCATEGORY_DISOBEY_SPEC: "Agent did not follow the task instructions",
    FailureCategory.SUBCATEGORY_MISSING_CONSTRAINT: "Agent violated implicit constraints",
    FailureCategory.SUBCATEGORY_REPETITION: "Agent repeated previously completed work",
    FailureCategory.SUBCATEGORY_NO_TERMINATION: "Agent failed to recognize task completion",
    FailureCategory.SUBCATEGORY_AMBIGUOUS_PROMPT: "Agent misinterpreted ambiguous instructions",
    FailureCategory.SUBCATEGORY_WRONG_TOOL: "Agent selected the wrong tool for the job",
    FailureCategory.SUBCATEGORY_WRONG_ARGS: "Agent provided incorrect arguments to a tool",
    FailureCategory.SUBCATEGORY_TOOL_NOT_FOUND: "Agent tried to use a non-existent tool",
    FailureCategory.SUBCATEGORY_IGNORE_OUTPUT: "Agent ignored or misinterpreted tool output",
    FailureCategory.SUBCATEGORY_REPEATED_TOOL_ERRORS: "Agent repeatedly failed with the same tool",
    FailureCategory.SUBCATEGORY_PREMATURE_STOP: "Agent stopped before completing the task",
    FailureCategory.SUBCATEGORY_NO_VALIDATION: "Agent did not verify its solution",
    FailureCategory.SUBCATEGORY_ACCEPTING_INCORRECT: "Agent accepted a wrong solution",
    FailureCategory.SUBCATEGORY_PARTIAL_SOLUTION: "Agent delivered incomplete work",
    FailureCategory.CODE_SYNTAX: "Generated code has syntax errors",
    FailureCategory.CODE_LOGIC: "Generated code has logical errors",
    FailureCategory.CODE_RUNTIME: "Generated code fails at runtime",
    FailureCategory.CODE_IMPORT: "Generated code has import/dependency errors",
    FailureCategory.CODE_TYPE: "Generated code has type errors",
    FailureCategory.ENV_SETUP: "Failed to set up execution environment",
    FailureCategory.ENV_MISSING_DEP: "Missing required dependencies",
    FailureCategory.ENV_PERMISSION: "Permission denied during execution",
    FailureCategory.ENV_TIMEOUT: "Execution timed out",
    FailureCategory.LLM_RATE_LIMIT: "Hit rate limit during LLM calls",
    FailureCategory.LLM_CONTEXT_WINDOW: "Exceeded LLM context window",
    FailureCategory.LLM_REFUSAL: "Model refused to perform the task",
    FailureCategory.LLM_HALLUCINATION: "Model hallucinated non-existent APIs or behavior",
    FailureCategory.PROMPT_TOO_VAGUE: "System prompt was too vague for the task",
    FailureCategory.PROMPT_TOO_LONG: "Context overflow due to excessive prompt length",
    FailureCategory.PROMPT_FORMAT: "Agent misunderstood required output format",
}


# Category groupings for high-level analysis
CATEGORY_GROUPS: dict[str, list[str]] = {
    "Specification Issues": [
        FailureCategory.SUBCATEGORY_DISOBEY_SPEC,
        FailureCategory.SUBCATEGORY_MISSING_CONSTRAINT,
        FailureCategory.SUBCATEGORY_REPETITION,
        FailureCategory.SUBCATEGORY_NO_TERMINATION,
        FailureCategory.SUBCATEGORY_AMBIGUOUS_PROMPT,
    ],
    "Tool Misalignment": [
        FailureCategory.SUBCATEGORY_WRONG_TOOL,
        FailureCategory.SUBCATEGORY_WRONG_ARGS,
        FailureCategory.SUBCATEGORY_TOOL_NOT_FOUND,
        FailureCategory.SUBCATEGORY_IGNORE_OUTPUT,
        FailureCategory.SUBCATEGORY_REPEATED_TOOL_ERRORS,
    ],
    "Verification Failures": [
        FailureCategory.SUBCATEGORY_PREMATURE_STOP,
        FailureCategory.SUBCATEGORY_NO_VALIDATION,
        FailureCategory.SUBCATEGORY_ACCEPTING_INCORRECT,
        FailureCategory.SUBCATEGORY_PARTIAL_SOLUTION,
    ],
    "Code Quality": [
        FailureCategory.CODE_SYNTAX,
        FailureCategory.CODE_LOGIC,
        FailureCategory.CODE_RUNTIME,
        FailureCategory.CODE_IMPORT,
        FailureCategory.CODE_TYPE,
    ],
    "Environment Issues": [
        FailureCategory.ENV_SETUP,
        FailureCategory.ENV_MISSING_DEP,
        FailureCategory.ENV_PERMISSION,
        FailureCategory.ENV_TIMEOUT,
    ],
    "LLM API Issues": [
        FailureCategory.LLM_RATE_LIMIT,
        FailureCategory.LLM_CONTEXT_WINDOW,
        FailureCategory.LLM_REFUSAL,
        FailureCategory.LLM_HALLUCINATION,
    ],
    "Prompt Engineering": [
        FailureCategory.PROMPT_TOO_VAGUE,
        FailureCategory.PROMPT_TOO_LONG,
        FailureCategory.PROMPT_FORMAT,
    ],
}

VALID_SEVERITIES = {"low", "medium", "high", "critical"}
ANALYSIS_STATE_SCHEMA_VERSION = 1
