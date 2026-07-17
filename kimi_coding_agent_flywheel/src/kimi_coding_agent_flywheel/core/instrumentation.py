"""Decorator-based telemetry instrumentation."""

from __future__ import annotations

import time
from typing import Any, Callable

from .tracing import Tracer

# -----------------------------------------------------------------------------
# Decorator-based instrumentation helpers
# -----------------------------------------------------------------------------

def instrument_llm_call(
    tracer: Tracer,
    model_attr: str = "model",
):
    """Decorator to instrument LLM API calls."""
    def decorator(func: Callable) -> Callable:
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.time()
            try:
                result = await func(*args, **kwargs)
                latency = (time.time() - start) * 1000

                # Extract messages from common patterns
                messages = kwargs.get("messages", args[1] if len(args) > 1 else [])
                if not isinstance(messages, list):
                    messages = []

                tracer.record_llm_call(
                    messages=messages,
                    response=str(result)[:2000],
                    model=kwargs.get(model_attr),
                    latency_ms=latency,
                )
                return result
            except Exception as e:
                latency = (time.time() - start) * 1000
                tracer.record_error(f"LLM call failed after {latency:.0f}ms", exception=e)
                raise

        return async_wrapper
    return decorator


def instrument_tool_call(tracer: Tracer):
    """Decorator to instrument tool function calls."""
    def decorator(func: Callable) -> Callable:
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.time()
            tool_name = func.__name__
            try:
                result = await func(*args, **kwargs)
                latency = (time.time() - start) * 1000
                tracer.record_tool_call(
                    tool_name=tool_name,
                    tool_input=kwargs,
                    tool_output=result,
                    latency_ms=latency,
                )
                return result
            except Exception as e:
                latency = (time.time() - start) * 1000
                tracer.record_tool_call(
                    tool_name=tool_name,
                    tool_input=kwargs,
                    tool_error=str(e),
                    latency_ms=latency,
                )
                raise

        return async_wrapper
    return decorator
