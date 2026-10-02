# LangSmith Mapping and Tracing

This document describes how the `major_proj` Customer Support Agent integrates with LangSmith for observability.

## Configuration

Tracing is enabled via `.env` variables:
- `LANGSMITH_TRACING=true` (which is mapped to `LANGCHAIN_TRACING_V2=true` under the hood in LangGraph)
- `LANGSMITH_PROJECT=CSA` (maps to `LANGCHAIN_PROJECT`)

## Verified Tracing Behavior

Based on actual inspection and evaluation of the baseline execution:

| LangSmith Concept | CSA Implementation | Description |
|-------------------|--------------------|-------------|
| **Project** | `CSA` | All traces route to the `CSA` project specified in `.env`. |
| **Trace** | `customer_support_graph.ainvoke(state)` | A single execution of the LangGraph workflow generates exactly one Trace. |
| **Run (Node)** | e.g., `route_intent_node`, `rag_node` | Each node executed within the graph appears as a child Run under the Trace. |
| **Sub-Run (LLM)**| `llm.ainvoke()` | Any LLM calls made within the nodes (like `classify_intent` or `generate_response`) appear as nested Runs. |
| **Thread** | NOT CONNECTED | Although the system has a `session_id`, it is **not** currently passed to LangSmith as a thread ID in `config={"configurable": {"thread_id": session_id}}`. |

## Thread Grouping Gap

> [!WARNING]
> **Thread Grouping is NOT CURRENTLY CONNECTED.** 
> While Traces and Runs exist per `ainvoke()` call, they are not linked together by `session_id` in LangSmith. Multi-turn conversations will appear as separate, independent traces. Wiring the Thread ID is documented as a known gap and must not be implemented until `CSA_BASELINE_V1` is fully established.

## Distinction from Local Tracking

The decorators `@track_llm_call` and `@track_tool_call` found in `app.middleware.tracking` are **local only**. They feed the in-process admin dashboard metrics (latency, errors, token count) and do **not** interact with LangSmith. LangSmith tracing happens entirely transparently via the LangChain/LangGraph SDKs.
