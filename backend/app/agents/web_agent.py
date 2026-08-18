"""
Web Search Agent — handles questions that fall outside the knowledge base
by searching the internet using the Web Search MCP tools.
"""

from app.config import settings
from app.llm_factory import get_llm
from app.middleware.tracking import track_llm_call

WEB_AGENT_SYSTEM_PROMPT = """You are a helpful customer support assistant. 

The customer asked a question that is outside our internal knowledge base.
You have performed a web search to find the answer.

TOOL RESULTS from web search:
{tool_results}

Rules:
1. Provide a helpful answer based ONLY on the web search results.
2. Do not invent information. If the search results don't contain the answer, apologize and say you couldn't find the information.
3. Maintain a polite and professional tone.
4. Keep the answer concise.
"""

def get_web_llm():
    """Get the large LLM for generating web search-based responses."""
    return get_llm(model=settings.llm_large_model, temperature=0.3)

def determine_search_query(message: str) -> dict:
    """
    Extract a search query from the customer's message.
    """
    # For now, we simply use the message as the query.
    # In a more advanced setup, an LLM could rewrite the query.
    return {"action": "web_search", "params": {"query": message}}

async def generate_response(
    message: str,
    tool_results: str,
    conversation_history: list[dict] = None,
) -> dict:
    """
    Generate a response using web search results.
    """
    llm = get_web_llm()

    history_text = ""
    if conversation_history:
        recent = conversation_history[-4:]
        for msg in recent:
            role = "Customer" if msg.get("role") == "customer" else "Agent"
            history_text += f"{role}: {msg.get('content', '')}\n"

    system_prompt = WEB_AGENT_SYSTEM_PROMPT.format(tool_results=tool_results)
    
    user_prompt = f"""Customer's message: "{message}"

{f"Conversation history:{chr(10)}{history_text}" if history_text else ""}

Provide a helpful response to the customer based on the web search results."""

    try:
        response = await llm.ainvoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        return {"response": response.content}
    except Exception as e:
        return {
            "response": "I apologize, but I am having trouble accessing the internet to find that information for you.",
            "error": str(e),
        }
