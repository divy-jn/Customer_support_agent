"""
Cost & Latency Tracking — wraps LLM calls with timing and token estimation.

Logs structured data to the JSON logger:
  - model name, node/step, tokens in/out, latency, estimated cost, retries, errors

Also collects aggregate metrics for the admin dashboard.
"""

import time
import logging
import functools
import threading
from contextlib import contextmanager
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger("tracking")


# ──────────────────────────────────────────────
#  Cost Estimation (per 1M tokens, approximate)
# ──────────────────────────────────────────────
MODEL_COSTS = {
    # Local Ollama models (free, but we track "equivalent" cost)
    "qwen2.5:3b": {"input": 0.0, "output": 0.0},
    "llama3.1:8b": {"input": 0.0, "output": 0.0},
    # Cloud fallbacks (if ever added)
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "claude-3.5-sonnet": {"input": 3.00, "output": 15.00},
}


def _estimate_tokens(text: str) -> int:
    """Rough token estimation: ~4 chars per token for English text."""
    return max(1, len(text) // 4)


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Estimate cost in USD based on model pricing."""
    costs = MODEL_COSTS.get(model, {"input": 0.0, "output": 0.0})
    input_cost = (tokens_in / 1_000_000) * costs["input"]
    output_cost = (tokens_out / 1_000_000) * costs["output"]
    return round(input_cost + output_cost, 6)


# ──────────────────────────────────────────────
#  Aggregate Metrics Collector (Thread-Safe)
# ──────────────────────────────────────────────
_lock = threading.Lock()

_aggregate_metrics = {
    "llm": {
        "total_calls": 0,
        "total_errors": 0,
        "total_tokens_in": 0,
        "total_tokens_out": 0,
        "total_latency_ms": 0.0,
        "total_estimated_cost_usd": 0.0,
        "by_model": defaultdict(lambda: {"calls": 0, "tokens_in": 0, "tokens_out": 0, "latency_ms": 0.0, "errors": 0}),
        "by_node": defaultdict(lambda: {"calls": 0, "latency_ms": 0.0, "errors": 0}),
    },
    "tools": {
        "total_calls": 0,
        "total_errors": 0,
        "total_latency_ms": 0.0,
        "by_tool": defaultdict(lambda: {"calls": 0, "latency_ms": 0.0, "errors": 0}),
    },
    "guardrails": {
        "total_input_checks": 0,
        "total_input_blocked": 0,
        "total_output_checks": 0,
        "total_output_blocked": 0,
        "violations_by_type": defaultdict(int),
    },
}


def get_aggregate_metrics() -> dict:
    """Return a JSON-safe copy of the aggregate metrics for the admin dashboard."""
    with _lock:
        return {
            "llm": {
                "total_calls": _aggregate_metrics["llm"]["total_calls"],
                "total_errors": _aggregate_metrics["llm"]["total_errors"],
                "total_tokens_in": _aggregate_metrics["llm"]["total_tokens_in"],
                "total_tokens_out": _aggregate_metrics["llm"]["total_tokens_out"],
                "total_latency_ms": round(_aggregate_metrics["llm"]["total_latency_ms"], 1),
                "avg_latency_ms": round(
                    _aggregate_metrics["llm"]["total_latency_ms"] / max(1, _aggregate_metrics["llm"]["total_calls"]),
                    1,
                ),
                "total_estimated_cost_usd": round(_aggregate_metrics["llm"]["total_estimated_cost_usd"], 6),
                "by_model": {k: dict(v) for k, v in _aggregate_metrics["llm"]["by_model"].items()},
                "by_node": {k: dict(v) for k, v in _aggregate_metrics["llm"]["by_node"].items()},
            },
            "tools": {
                "total_calls": _aggregate_metrics["tools"]["total_calls"],
                "total_errors": _aggregate_metrics["tools"]["total_errors"],
                "total_latency_ms": round(_aggregate_metrics["tools"]["total_latency_ms"], 1),
                "avg_latency_ms": round(
                    _aggregate_metrics["tools"]["total_latency_ms"] / max(1, _aggregate_metrics["tools"]["total_calls"]),
                    1,
                ),
                "by_tool": {k: dict(v) for k, v in _aggregate_metrics["tools"]["by_tool"].items()},
            },
            "guardrails": {
                "total_input_checks": _aggregate_metrics["guardrails"]["total_input_checks"],
                "total_input_blocked": _aggregate_metrics["guardrails"]["total_input_blocked"],
                "total_output_checks": _aggregate_metrics["guardrails"]["total_output_checks"],
                "total_output_blocked": _aggregate_metrics["guardrails"]["total_output_blocked"],
                "block_rate": round(
                    _aggregate_metrics["guardrails"]["total_input_blocked"]
                    / max(1, _aggregate_metrics["guardrails"]["total_input_checks"])
                    * 100,
                    1,
                ),
                "violations_by_type": dict(_aggregate_metrics["guardrails"]["violations_by_type"]),
            },
        }


def record_guardrail_input(blocked: bool, violations: list[str]):
    """Record an input guardrail check."""
    with _lock:
        _aggregate_metrics["guardrails"]["total_input_checks"] += 1
        if blocked:
            _aggregate_metrics["guardrails"]["total_input_blocked"] += 1
        for v in violations:
            _aggregate_metrics["guardrails"]["violations_by_type"][v] += 1


def record_guardrail_output(blocked: bool, violations: list[str]):
    """Record an output guardrail check."""
    with _lock:
        _aggregate_metrics["guardrails"]["total_output_checks"] += 1
        if blocked:
            _aggregate_metrics["guardrails"]["total_output_blocked"] += 1
        for v in violations:
            _aggregate_metrics["guardrails"]["violations_by_type"][v] += 1


# ──────────────────────────────────────────────
#  Tracking Context Manager
# ──────────────────────────────────────────────

@contextmanager
def track_llm_call(model: str, node: str, prompt_text: str = ""):
    """
    Context manager that tracks the latency and logs metadata for an LLM call.

    Usage:
        with track_llm_call("llama3.1:8b", "rag_node", prompt) as tracker:
            response = llm.invoke(messages)
            tracker["output_text"] = response.content
    """
    tracker = {
        "model": model,
        "node": node,
        "tokens_in": _estimate_tokens(prompt_text),
        "output_text": "",
        "error": None,
        "retries": 0,
    }

    start_time = time.perf_counter()

    try:
        yield tracker
    except Exception as e:
        tracker["error"] = str(e)
        raise
    finally:
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
        tokens_out = _estimate_tokens(tracker.get("output_text", ""))
        cost = _estimate_cost(model, tracker["tokens_in"], tokens_out)

        log_data = {
            "model": model,
            "node": node,
            "tokens_in": tracker["tokens_in"],
            "tokens_out": tokens_out,
            "latency_ms": elapsed_ms,
            "estimated_cost_usd": cost,
            "retries": tracker.get("retries", 0),
            "error": tracker.get("error"),
        }

        if tracker.get("error"):
            logger.error("LLM call failed", extra=log_data)
        else:
            logger.info("LLM call completed", extra=log_data)

        # Update aggregate metrics
        with _lock:
            m = _aggregate_metrics["llm"]
            m["total_calls"] += 1
            m["total_tokens_in"] += tracker["tokens_in"]
            m["total_tokens_out"] += tokens_out
            m["total_latency_ms"] += elapsed_ms
            m["total_estimated_cost_usd"] += cost

            if tracker.get("error"):
                m["total_errors"] += 1

            bm = m["by_model"][model]
            bm["calls"] += 1
            bm["tokens_in"] += tracker["tokens_in"]
            bm["tokens_out"] += tokens_out
            bm["latency_ms"] += elapsed_ms
            if tracker.get("error"):
                bm["errors"] += 1

            bn = m["by_node"][node]
            bn["calls"] += 1
            bn["latency_ms"] += elapsed_ms
            if tracker.get("error"):
                bn["errors"] += 1


# ──────────────────────────────────────────────
#  Tool Call Tracker
# ──────────────────────────────────────────────

def track_tool_call(tool_name: str):
    """
    Decorator that tracks tool execution time and errors.

    Usage:
        @track_tool_call("lookup_customer")
        def lookup_customer(identifier):
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            error = None
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                error = str(e)
                raise
            finally:
                elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
                logger.info(
                    f"Tool call: {tool_name}",
                    extra={
                        "tool": tool_name,
                        "latency_ms": elapsed_ms,
                        "error": error,
                        "args_preview": str(kwargs)[:200] if kwargs else str(args)[:200],
                    },
                )

                # Update aggregate metrics
                with _lock:
                    t = _aggregate_metrics["tools"]
                    t["total_calls"] += 1
                    t["total_latency_ms"] += elapsed_ms
                    if error:
                        t["total_errors"] += 1

                    bt = t["by_tool"][tool_name]
                    bt["calls"] += 1
                    bt["latency_ms"] += elapsed_ms
                    if error:
                        bt["errors"] += 1

        return wrapper
    return decorator
