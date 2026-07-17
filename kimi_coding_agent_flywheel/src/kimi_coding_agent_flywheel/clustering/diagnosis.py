"""LLM-judge diagnosis for canonical agent traces."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from .models import (
    DiagnosedFailures,
    DiagnosisInfrastructureError,
    DiagnosisInvalidResponse,
    DiagnosisResult,
    FailureInstance,
)
from .taxonomy import CATEGORY_GROUPS, FAILURE_DESCRIPTIONS, VALID_SEVERITIES, FailureCategory

# -----------------------------------------------------------------------------
# LLM-as-Judge Failure Diagnoser
# -----------------------------------------------------------------------------

class LLMJudgeDiagnoser:
    """
    Uses an LLM to diagnose failures from execution traces.

    Inspired by Composo.ai's criteria-less judging approach:
    - No static rubric that misses novel failure modes
    - Let the judge infer what a competent agent would do
    - Extract freeform diagnostic text for clustering
    """

    DIAGNOSIS_PROMPT_TEMPLATE = """You are an expert AI systems debugger.

Analyze the following agent execution trace and identify ALL failures.

AGENT: {agent_name}
MODEL: {model_id}
TASK: {task_description}

SYSTEM PROMPT:
```
{system_prompt}
```

EXECUTION TRACE:
```
{trace_snippet}
```

ERROR OUTPUT:
```
{error_output}
```

Your analysis should:
1. Identify what a competent agent would have done for this task
2. Compare the actual agent's behavior against that ideal
3. For each failure, provide:
   - The specific failure type (choose from: {failure_types})
   - Severity (low/medium/high/critical)
   - A 2-3 sentence diagnostic description
   - The likely root cause (prompt issue, tool issue, model limitation, etc.)
   - A specific suggestion for fixing it

4. Score the overall execution from 0-10

Format your response as JSON:
{{
  "overall_score": float,
  "failures": [
    {{
      "subcategory": str,
      "severity": str,
      "description": str,
      "root_cause": str,
      "suggested_fix": str,
      "affected_prompt_component": str | null
    }}
  ],
  "summary": str
}}
"""

    def __init__(
        self,
        judge_fn: Callable[[str], str] | None = None,
        *,
        use_mock_judge: bool = False,
    ):
        """
        Args:
            judge_fn: Function that takes a prompt string and returns the judge's response.
            use_mock_judge: Explicitly opt into the built-in testing mock.
        """
        if judge_fn is None and not use_mock_judge:
            raise ValueError(
                "LLMJudgeDiagnoser requires an explicit judge_fn. "
                "Pass use_mock_judge=True only in tests or demos."
            )
        self.judge_fn = judge_fn or self._mock_judge
        self._failure_type_list = ", ".join(FAILURE_DESCRIPTIONS.keys())

    def diagnose(self, trace_data: dict[str, Any], task_description: str = "") -> DiagnosisResult:
        """
        Diagnose failures from a single execution trace.

        Returns a discriminated result. Judge infrastructure failures and invalid
        judge responses are not converted into agent failures.
        """
        # Build the diagnosis prompt
        prompt = self.DIAGNOSIS_PROMPT_TEMPLATE.format(
            agent_name=trace_data.get("agent_name", "unknown"),
            model_id=trace_data.get("model_id", "unknown"),
            task_description=task_description,
            system_prompt=(trace_data.get("system_prompt") or "N/A")[:2000],
            trace_snippet=self._extract_relevant_trace(trace_data)[:3000],
            error_output=self._extract_errors(trace_data),
            failure_types=self._failure_type_list,
        )

        # Call the judge
        try:
            # Egress Redaction: enforce safety pass immediately before LLM call
            from agent_quality.privacy.redaction import redact_text
            redacted_prompt = redact_text(prompt).value
            response = self.judge_fn(redacted_prompt)
        except Exception as e:
            return DiagnosisInfrastructureError(
                trace_id=trace_data.get("trace_id"),
                task_id=str(trace_data.get("task_id", "unknown")),
                agent_name=str(trace_data.get("agent_name", "unknown")),
                message=str(e),
                exception_type=type(e).__name__,
            )

        return self._parse_judge_response(response, trace_data)

    def _extract_relevant_trace(self, trace_data: dict[str, Any]) -> str:
        """Extract the most relevant portion of the trace for diagnosis."""
        events = trace_data.get("events", [])

        # Prioritize error events and their context
        relevant_events = []
        for i, event in enumerate(events):
            if event.get("event_type") in ["ERROR", "TOOL_CALL", "LLM_RESPONSE"]:
                # Include some context around the error
                start = max(0, i - 2)
                end = min(len(events), i + 3)
                for j in range(start, end):
                    if events[j] not in relevant_events:
                        relevant_events.append(events[j])

        # If no errors found, include last 10 events
        if not relevant_events:
            relevant_events = events[-10:]

        # Format events
        lines = []
        for event in relevant_events:
            event_type = event.get("event_type", "UNKNOWN")
            content = event.get("content", "")
            tool_name = event.get("tool_name", "")
            tool_error = event.get("tool_error", "")

            if tool_name:
                lines.append(f"[{event_type}] Tool: {tool_name}")
                if tool_error:
                    lines.append(f"  ERROR: {tool_error}")
            else:
                lines.append(f"[{event_type}] {content[:500]}")

        return "\n".join(lines)

    def _extract_errors(self, trace_data: dict[str, Any]) -> str:
        """Extract all error messages from the trace."""
        errors = []
        for event in trace_data.get("events", []):
            if event.get("event_type") == "ERROR":
                errors.append(event.get("content", ""))
            if event.get("tool_error"):
                errors.append(f"Tool {event.get('tool_name')}: {event.get('tool_error')}")
        return "\n".join(errors) if errors else "No explicit errors found"

    def _parse_judge_response(self, response: str, trace_data: dict[str, Any]) -> DiagnosisResult:
        """Parse the judge's JSON response into FailureInstance objects."""
        if not isinstance(response, str):
            return self._invalid_response(
                trace_data,
                f"Judge response must be a string, got {type(response).__name__}",
                repr(response),
            )

        # Try to extract JSON from response
        try:
            # Find JSON block
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = json.loads(response)
        except json.JSONDecodeError as e:
            return self._invalid_response(
                trace_data,
                f"Judge response was not valid JSON: {e.msg}",
                response,
            )

        if not isinstance(data, dict):
            return self._invalid_response(trace_data, "Judge response JSON must be an object", response)

        overall_score = data.get("overall_score")
        if not isinstance(overall_score, (int, float)) or not 0 <= float(overall_score) <= 10:
            return self._invalid_response(
                trace_data,
                "Judge response overall_score must be a number from 0 to 10",
                response,
            )

        failure_items = data.get("failures")
        if not isinstance(failure_items, list):
            return self._invalid_response(trace_data, "Judge response failures must be a list", response)

        failures = []
        for i, failure_data in enumerate(failure_items):
            if not isinstance(failure_data, dict):
                return self._invalid_response(
                    trace_data,
                    f"Judge response failure at index {i} must be an object",
                    response,
                )

            subcategory = failure_data.get("subcategory")
            if not isinstance(subcategory, str) or subcategory not in FAILURE_DESCRIPTIONS:
                return self._invalid_response(
                    trace_data,
                    f"Judge response failure at index {i} has unknown subcategory",
                    response,
                )

            severity = failure_data.get("severity")
            if not isinstance(severity, str) or severity.lower() not in VALID_SEVERITIES:
                return self._invalid_response(
                    trace_data,
                    f"Judge response failure at index {i} has invalid severity",
                    response,
                )

            category = self._subcategory_to_category(subcategory)

            fi = FailureInstance(
                failure_id=self._failure_id(trace_data, failure_data, i),
                task_id=str(trace_data.get("task_id", "unknown")),
                agent_name=str(trace_data.get("agent_name", "unknown")),
                model_id=trace_data.get("model_id"),
                category=category,
                subcategory=subcategory,
                description=str(failure_data.get("description", "")),
                severity=severity.lower(),
                trace_id=trace_data.get("trace_id"),
                probable_cause=str(failure_data.get("root_cause", "")),
                suggested_fix=str(failure_data.get("suggested_fix", "")),
                affected_prompt_component=failure_data.get("affected_prompt_component"),
                llm_judge_score=float(overall_score),
            )
            failures.append(fi)

        return DiagnosedFailures(failures)

    def _failure_id(self, trace_data: dict[str, Any], failure_data: dict[str, Any], index: int) -> str:
        """Build a stable failure id with a content hash to avoid unknown-trace collisions."""
        trace_part = str(trace_data.get("trace_id") or "unknown")
        payload = {
            "trace_id": trace_data.get("trace_id"),
            "task_id": trace_data.get("task_id"),
            "agent_name": trace_data.get("agent_name"),
            "index": index,
            "failure": failure_data,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:12]
        return f"diag_{trace_part}_{index}_{digest}"

    def _invalid_response(
        self,
        trace_data: dict[str, Any],
        message: str,
        response: str,
    ) -> DiagnosisInvalidResponse:
        return DiagnosisInvalidResponse(
            trace_id=trace_data.get("trace_id"),
            task_id=str(trace_data.get("task_id", "unknown")),
            agent_name=str(trace_data.get("agent_name", "unknown")),
            message=message,
            response_excerpt=response[:500],
        )

    def _subcategory_to_category(self, subcategory: str) -> str:
        """Map a subcategory to its parent category."""
        for category, subcategories in CATEGORY_GROUPS.items():
            if subcategory in subcategories:
                return category
        return "unknown"

    def _mock_judge(self, prompt: str) -> str:
        """Mock judge for testing without LLM access."""
        # Simple heuristic-based diagnosis
        prompt_lower = prompt.lower()

        failures = []

        if "syntax error" in prompt_lower or "indentation" in prompt_lower:
            failures.append({
                "subcategory": FailureCategory.CODE_SYNTAX,
                "severity": "high",
                "description": "Generated code contains syntax errors that prevent execution.",
                "root_cause": "Model produced malformed code, possibly due to insufficient examples in prompt",
                "suggested_fix": "Add syntax validation step and examples of correct code structure in system prompt",
                "affected_prompt_component": "system_prompt",
            })

        if "tool" in prompt_lower and ("not found" in prompt_lower or "error" in prompt_lower):
            failures.append({
                "subcategory": FailureCategory.SUBCATEGORY_WRONG_TOOL,
                "severity": "medium",
                "description": "Agent selected incorrect tool or provided wrong arguments.",
                "root_cause": "Tool descriptions may be ambiguous or agent lacks understanding of tool capabilities",
                "suggested_fix": "Improve tool descriptions with usage examples and expected inputs/outputs",
                "affected_prompt_component": "tool_definitions",
            })

        if "timeout" in prompt_lower:
            failures.append({
                "subcategory": FailureCategory.ENV_TIMEOUT,
                "severity": "medium",
                "description": "Task execution exceeded time limit.",
                "root_cause": "Agent may be stuck in a loop or performing inefficient operations",
                "suggested_fix": "Add step limit and early termination logic to system prompt",
                "affected_prompt_component": "system_prompt",
            })

        if not failures:
            failures.append({
                "subcategory": FailureCategory.SUBCATEGORY_NO_VALIDATION,
                "severity": "medium",
                "description": "Agent may not have properly validated its solution.",
                "root_cause": "Missing verification steps in agent's workflow",
                "suggested_fix": "Add explicit validation requirements to system prompt",
                "affected_prompt_component": "system_prompt",
            })

        return json.dumps({
            "overall_score": 4.0,
            "failures": failures,
            "summary": f"Identified {len(failures)} failure patterns in agent execution.",
        })
