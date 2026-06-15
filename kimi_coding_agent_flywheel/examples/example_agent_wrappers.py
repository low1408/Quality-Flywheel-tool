"""
Example Agent Wrappers for Popular Coding Agents.

Shows how to integrate Codex CLI, Claude Code, and OpenHands
with the quality flywheel's telemetry and benchmarking system.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.benchmark import AgentOutput, BenchmarkTask, CodingAgent, TaskId
from core.telemetry import Tracer


class CodexCLIWrapper(CodingAgent):
    """
    Wrapper for OpenAI's Codex CLI agent.

    Captures all tool calls, code output, and execution traces
    for quality flywheel analysis.
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        system_prompt: str | None = None,
        approval_mode: str = "auto",
    ):
        self.model = model
        self._system_prompt = system_prompt or self._default_prompt()
        self.approval_mode = approval_mode
        self.tracer = Tracer(agent_name="codex-cli", model_id=model)

    @property
    def name(self) -> str:
        return "codex-cli"

    @property
    def model_id(self) -> str | None:
        return self.model

    def get_system_prompt(self) -> str:
        return self._system_prompt

    def update_system_prompt(self, new_prompt: str) -> None:
        self._system_prompt = new_prompt

    async def execute(self, task: BenchmarkTask) -> AgentOutput:
        """Execute Codex CLI on a benchmark task with full telemetry."""
        output = AgentOutput(
            task_id=task.task_id,
            agent_name=self.name,
            model_id=self.model,
            system_prompt=self._system_prompt,
        )

        with self.tracer.trace(task_id=str(task.task_id), model_params={"model": self.model}) as trace:
            # Record system prompt
            trace.system_prompt = self._system_prompt

            try:
                # Write task instruction to a temporary file
                task_file = Path(f"/tmp/codex_task_{task.task_id.stable_id}.md")
                task_file.write_text(self._format_task_for_codex(task))

                # Prepare Codex CLI command
                cmd = [
                    "codex",
                    "--model", self.model,
                    "--approval-mode", self.approval_mode,
                    "-q",  # Quiet mode
                    "-f", str(task_file),
                ]

                # Add context files if available
                if task.context_files:
                    context_dir = Path(f"/tmp/codex_context_{task.task_id.stable_id}")
                    context_dir.mkdir(exist_ok=True)
                    for filename, content in task.context_files.items():
                        (context_dir / filename).write_text(content)

                # Run Codex CLI
                start_time = datetime.utcnow()
                trace.record_metric("execution_start", 0.0)

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=task.estimated_duration_sec,
                    cwd=str(context_dir) if task.context_files else None,
                )

                end_time = datetime.utcnow()
                duration = (end_time - start_time).total_seconds()

                # Record execution
                trace.record_metric("execution_duration", duration, "seconds")
                trace.record_metric("return_code", result.returncode)

                if result.returncode == 0:
                    output.final_code = result.stdout
                    trace.record_metric("success", 1.0)
                else:
                    trace.record_error(f"Codex CLI failed: {result.stderr}")
                    output.final_code = result.stdout

                # Capture stdout/stderr
                if result.stdout:
                    trace.record_thought(f"Codex output:\n{result.stdout[:2000]}")
                if result.stderr:
                    trace.record_error(f"Codex stderr: {result.stderr[:1000]}")

                # Record tool calls from stdout parsing
                # (Codex CLI outputs tool calls in a structured format)
                self._parse_codex_output(result.stdout, trace)

                output.end_time = end_time

            except subprocess.TimeoutExpired:
                trace.record_error("Codex CLI execution timed out")
                output.end_time = datetime.utcnow()
            except Exception as e:
                trace.record_error("Codex CLI execution failed", exception=e)
                output.end_time = datetime.utcnow()

        return output

    def _format_task_for_codex(self, task: BenchmarkTask) -> str:
        """Format a benchmark task for Codex CLI input."""
        content = f"""# Task: {task.task_id}

{task.instruction}

## Requirements
"""
        for criterion in task.success_criteria:
            content += f"- {criterion}\n"

        if task.context_files:
            content += "\n## Context Files\n"
            for filename, file_content in task.context_files.items():
                content += f"\n### {filename}\n```\n{file_content[:2000]}\n```\n"

        return content

    def _parse_codex_output(self, output: str, trace: Any) -> None:
        """Parse Codex CLI output to extract tool calls."""
        # Codex CLI outputs structured tool call information
        # Look for patterns like: "[file:write] path/to/file" or "[cmd:run] command"

        tool_patterns = [
            (r'\[file:(\w+)\]\s*(.+)', 'file'),
            (r'\[cmd:(\w+)\]\s*(.+)', 'command'),
            (r'\[edit:(\w+)\]\s*(.+)', 'edit'),
        ]

        import re
        for pattern, tool_category in tool_patterns:
            for match in re.finditer(pattern, output, re.MULTILINE):
                action = match.group(1)
                details = match.group(2)
                trace.record_tool_call(
                    tool_name=f"{tool_category}_{action}",
                    tool_input={"details": details},
                    tool_output="executed",
                )

    def _default_prompt(self) -> str:
        return """You are Codex, a coding assistant. Follow these rules:
1. Write clean, well-documented code
2. Handle edge cases and errors
3. Follow existing code style
4. Validate your solution works"""


class ClaudeCodeWrapper(CodingAgent):
    """
    Wrapper for Anthropic's Claude Code agent.

    Uses the Claude Code CLI with full trace capture.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4",
        system_prompt: str | None = None,
    ):
        self.model = model
        self._system_prompt = system_prompt or self._default_prompt()
        self.tracer = Tracer(agent_name="claude-code", model_id=model)

    @property
    def name(self) -> str:
        return "claude-code"

    @property
    def model_id(self) -> str | None:
        return self.model

    def get_system_prompt(self) -> str:
        return self._system_prompt

    def update_system_prompt(self, new_prompt: str) -> None:
        self._system_prompt = new_prompt

    async def execute(self, task: BenchmarkTask) -> AgentOutput:
        """Execute Claude Code on a benchmark task."""
        output = AgentOutput(
            task_id=task.task_id,
            agent_name=self.name,
            model_id=self.model,
            system_prompt=self._system_prompt,
        )

        with self.tracer.trace(task_id=str(task.task_id)) as trace:
            trace.system_prompt = self._system_prompt

            try:
                # Format task for Claude Code
                prompt_text = self._format_task_for_claude(task)

                # Run Claude Code CLI
                cmd = [
                    "claude",
                    "--model", self.model,
                    "--output-format", "stream-json",
                    prompt_text,
                ]

                start_time = datetime.utcnow()

                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=1024 * 1024,  # 1MB buffer
                )

                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=task.estimated_duration_sec,
                )

                end_time = datetime.utcnow()

                stdout_text = stdout.decode("utf-8", errors="replace")
                stderr_text = stderr.decode("utf-8", errors="replace")

                # Parse Claude Code's streaming JSON output
                self._parse_claude_output(stdout_text, trace, output)

                if process.returncode == 0:
                    trace.record_metric("success", 1.0)
                else:
                    trace.record_error(f"Claude Code exited with code {process.returncode}")

                if stderr_text:
                    trace.record_error(f"stderr: {stderr_text[:1000]}")

                output.end_time = end_time

            except asyncio.TimeoutError:
                trace.record_error("Claude Code execution timed out")
                output.end_time = datetime.utcnow()
            except Exception as e:
                trace.record_error("Claude Code execution failed", exception=e)
                output.end_time = datetime.utcnow()

        return output

    def _format_task_for_claude(self, task: BenchmarkTask) -> str:
        """Format task for Claude Code CLI."""
        prompt = task.instruction

        if task.context_files:
            prompt += "\n\nContext files:\n"
            for filename, content in task.context_files.items():
                prompt += f"\n--- {filename} ---\n{content[:3000]}\n"

        if task.success_criteria:
            prompt += "\n\nSuccess criteria:\n"
            for criterion in task.success_criteria:
                prompt += f"- {criterion}\n"

        return prompt

    def _parse_claude_output(self, output: str, trace: Any, agent_output: AgentOutput) -> None:
        """Parse Claude Code's JSON stream output."""
        import re

        # Look for code blocks
        code_blocks = re.findall(r'```(?:\w+)?\n(.*?)```', output, re.DOTALL)
        if code_blocks:
            agent_output.final_code = code_blocks[-1]  # Last code block is usually the solution

        # Look for tool call patterns in Claude output
        # Claude Code uses XML-style tags for tool calls
        tool_calls = re.findall(r'<tool>(\w+)</tool>\s*<input>(.*?)</input>', output, re.DOTALL)
        for tool_name, tool_input in tool_calls:
            trace.record_tool_call(
                tool_name=tool_name,
                tool_input={"input": tool_input[:500]},
                tool_output="executed",
            )

        # Record the full output as a thought
        trace.record_thought(f"Claude output:\n{output[:3000]}")

    def _default_prompt(self) -> str:
        return """You are Claude Code, an expert software engineer. Follow these principles:
1. Write production-quality code with proper error handling
2. Add type hints and docstrings
3. Consider edge cases and security implications
4. Write tests for your code
5. Explain your reasoning"""


class OpenHandsWrapper(CodingAgent):
    """
    Wrapper for OpenHands (formerly OpenDevin) coding agent.

    Uses the OpenHands SDK or CLI for benchmark execution.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4",
        system_prompt: str | None = None,
        runtime: str = "docker",  # or "local"
    ):
        self.model = model
        self._system_prompt = system_prompt or self._default_prompt()
        self.runtime = runtime
        self.tracer = Tracer(agent_name="openhands", model_id=model)

    @property
    def name(self) -> str:
        return "openhands"

    @property
    def model_id(self) -> str | None:
        return self.model

    def get_system_prompt(self) -> str:
        return self._system_prompt

    def update_system_prompt(self, new_prompt: str) -> None:
        self._system_prompt = new_prompt

    async def execute(self, task: BenchmarkTask) -> AgentOutput:
        """Execute OpenHands on a benchmark task."""
        output = AgentOutput(
            task_id=task.task_id,
            agent_name=self.name,
            model_id=self.model,
            system_prompt=self._system_prompt,
        )

        with self.tracer.trace(task_id=str(task.task_id)) as trace:
            trace.system_prompt = self._system_prompt

            try:
                # OpenHands can be invoked via CLI or Python SDK
                # Here we use the CLI approach

                # Write task to file
                task_file = Path(f"/tmp/oh_task_{task.task_id.stable_id}.txt")
                task_file.write_text(task.instruction)

                # Build command
                cmd = [
                    "python", "-m", "openhands.core.main",
                    "-t", task.instruction,
                    "-c", self._get_config_path(),
                    "-m", self.model,
                ]

                if self.runtime == "docker":
                    cmd.extend(["--runtime", "docker"])

                start_time = datetime.utcnow()

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=task.estimated_duration_sec,
                )

                end_time = datetime.utcnow()

                # Parse OpenHands output (it produces structured logs)
                self._parse_openhands_output(result.stdout, trace, output)

                if result.returncode == 0:
                    trace.record_metric("success", 1.0)
                    # Extract final code from output
                    output.final_code = self._extract_code_from_output(result.stdout)
                else:
                    trace.record_error(f"OpenHands failed: {result.stderr[:1000]}")

                output.end_time = end_time

            except subprocess.TimeoutExpired:
                trace.record_error("OpenHands execution timed out")
                output.end_time = datetime.utcnow()
            except Exception as e:
                trace.record_error("OpenHands execution failed", exception=e)
                output.end_time = datetime.utcnow()

        return output

    def _parse_openhands_output(self, output: str, trace: Any, agent_output: AgentOutput) -> None:
        """Parse OpenHands structured output."""
        import re

        # OpenHands outputs action/observation pairs
        actions = re.findall(r'ACTION:\s*(.+?)(?=\nOBSERVATION:|$)', output, re.DOTALL)
        observations = re.findall(r'OBSERVATION:\s*(.+?)(?=\nACTION:|$)', output, re.DOTALL)

        for i, action in enumerate(actions):
            trace.record_thought(f"Action {i}: {action[:500]}")
            if i < len(observations):
                trace.record_thought(f"Observation {i}: {observations[i][:500]}")

        # Extract tool calls
        tool_patterns = [
            r'run_cmd\("(.+?)"\)',
            r'write\("(.+?)",\s*"(.+?)"\)',
            r'read\("(.+?)"\)',
        ]
        for pattern in tool_patterns:
            for match in re.finditer(pattern, output):
                trace.record_tool_call(
                    tool_name=pattern.split("(")[0],
                    tool_input={"args": match.groups()},
                    tool_output="executed",
                )

    def _extract_code_from_output(self, output: str) -> str | None:
        """Extract the final code from OpenHands output."""
        import re
        code_blocks = re.findall(r'```(?:\w+)?\n(.*?)```', output, re.DOTALL)
        return code_blocks[-1] if code_blocks else None

    def _get_config_path(self) -> str:
        """Get or create OpenHands config."""
        config = {
            "model": self.model,
            "runtime": self.runtime,
            "system_prompt": self._system_prompt,
        }
        config_path = Path("/tmp/oh_config.json")
        config_path.write_text(json.dumps(config))
        return str(config_path)

    def _default_prompt(self) -> str:
        return """You are an autonomous software engineering agent.
Your goal is to complete coding tasks by:
1. Understanding the requirements
2. Exploring the codebase
3. Planning your approach
4. Implementing the solution
5. Testing and validating
6. Reporting results

Use the available tools effectively and learn from feedback."""


# -----------------------------------------------------------------------------
# Mock Agent for Testing
# -----------------------------------------------------------------------------

class MockCodingAgent(CodingAgent):
    """A mock agent for testing the quality flywheel without API access."""

    def __init__(
        self,
        name: str = "mock-agent",
        model_id: str | None = None,
        system_prompt: str = "You are a coding assistant.",
        simulate_success_rate: float = 0.7,
    ):
        self._name = name
        self._model_id = model_id or "mock-model"
        self._system_prompt = system_prompt
        self.success_rate = simulate_success_rate
        self.tracer = Tracer(agent_name=name, model_id=self._model_id)
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def model_id(self) -> str | None:
        return self._model_id

    def get_system_prompt(self) -> str:
        return self._system_prompt

    def update_system_prompt(self, new_prompt: str) -> None:
        self._system_prompt = new_prompt

    async def execute(self, task: BenchmarkTask) -> AgentOutput:
        """Simulate agent execution."""
        import random

        self.call_count += 1
        output = AgentOutput(
            task_id=task.task_id,
            agent_name=self.name,
            model_id=self.model_id,
            system_prompt=self._system_prompt,
        )

        with self.tracer.trace(task_id=str(task.task_id)) as trace:
            trace.system_prompt = self._system_prompt

            # Simulate thinking
            trace.record_thought(f"Analyzing task: {task.instruction[:100]}...")

            # Simulate tool calls
            tools_used = ["file_read", "code_write", "test_run"]
            for tool in tools_used:
                trace.record_tool_call(
                    tool_name=tool,
                    tool_input={"task": str(task.task_id)},
                    tool_output="success" if random.random() < self.success_rate else "error",
                )

            # Simulate code generation
            output.final_code = f"# Generated code for {task.task_id}\ndef solution():\n    pass\n"

            # Simulate success/failure based on configured rate
            success = random.random() < self.success_rate
            if not success:
                trace.record_error("Simulated failure for testing")

            trace.record_metric("success", 1.0 if success else 0.0)
            output.end_time = datetime.utcnow()

        return output
