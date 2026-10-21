"""General-support agent constrained to the approved knowledge base."""

from typing import Any

from app.agents import rag_agent


async def handle_general_request(state: dict[str, Any]) -> dict[str, Any]:
    """Answer only from RAG; never grant transactional capabilities here."""
    result = await rag_agent.generate_response(
        message=state["message"],
        conversation_history=state.get("conversation_history", []),
    )
    return {
        "response": result["response"],
        "tool_results": {"sources": result.get("sources", []), "context_used": result.get("context_used", False)},
        "escalated": False,
    }
