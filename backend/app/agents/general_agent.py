import re
from typing import Any

from app.agents import rag_agent

_NAME_QUERY = re.compile(
    r"\b(what('s| is) my name|who am i|my name|do you know my name|what is my account name|my account name|account name|what is my user name|my username)\b",
    re.IGNORECASE,
)


async def handle_general_request(state: dict[str, Any]) -> dict[str, Any]:
    """Answer only from RAG; never grant transactional capabilities here."""
    msg = state.get("message", "")
    cust_name = state.get("customer_name")
    cust_id = state.get("customer_id")
    intent = state.get("intent", "").lower()

    is_name_query = bool(_NAME_QUERY.search(msg)) or intent in {"inquire_user_name", "inquire_account_name", "account_name"}
    if is_name_query:
        if not cust_name and cust_id:
            try:
                from app.tools import supabase
                res = supabase.table("customers").select("name").eq("id", cust_id).execute()
                if res.data and res.data[0].get("name"):
                    cust_name = res.data[0]["name"]
            except Exception:
                pass
        if cust_name:
            return {
                "response": f"You are logged in as {cust_name}.",
                "tool_results": {"source": "authenticated_session"},
                "escalated": False,
            }
        elif cust_id:
            return {
                "response": f"You are logged in with customer ID #{cust_id}.",
                "tool_results": {"source": "authenticated_session"},
                "escalated": False,
            }

    result = await rag_agent.generate_response(
        message=state["message"],
        conversation_history=state.get("conversation_history", []),
    )
    return {
        "response": result["response"],
        "tool_results": {"sources": result.get("sources", []), "context_used": result.get("context_used", False)},
        "escalated": False,
    }
