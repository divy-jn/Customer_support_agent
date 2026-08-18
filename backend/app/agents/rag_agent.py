"""
RAG Agent — answers customer questions using the ChromaDB knowledge base.

Implements zero-hallucination by strictly grounding responses in
retrieved context. If no relevant context is found, the agent
explicitly says it doesn't have the information.
"""

from app.config import settings
from app.rag.retriever import retrieve_as_context
from app.llm_factory import get_llm
from app.middleware.tracking import track_llm_call


RAG_SYSTEM_PROMPT = """You are a helpful, professional customer support assistant.

You MUST follow these rules strictly:
1. ONLY answer based on the provided CONTEXT below. Do NOT use any external knowledge.
2. If the CONTEXT does not contain the answer, say: "I don't have specific information about that in our knowledge base. Let me connect you with a human agent who can help."
3. Be concise, friendly, and professional.
4. When referencing policies (return window, warranty period, etc.), quote the exact numbers from the context.
5. If the customer seems frustrated, acknowledge their feelings before providing the answer.
6. Always end with an offer to help further.

CONTEXT:
{context}

Remember: You are NOT allowed to make up information. Only use what is provided in the CONTEXT above."""


def get_rag_llm():
    """Get the large LLM for generating RAG responses."""
    return get_llm(model=settings.llm_large_model, temperature=0.3)


async def generate_response(
    message: str,
    conversation_history: list[dict] = None,
    top_k: int = 5,
) -> dict:
    """
    Generate a RAG-grounded response to a customer query.

    Args:
        message: The customer's question.
        conversation_history: Previous messages for context.
        top_k: Number of knowledge base chunks to retrieve.

    Returns:
        Dict with response text, sources used, and retrieval metadata.
    """
    # Step 1: Retrieve relevant context from ChromaDB
    context = retrieve_as_context(message, top_k=top_k)

    # Step 2: Build conversation context
    history_text = ""
    if conversation_history:
        recent = conversation_history[-6:]
        for msg in recent:
            role = "Customer" if msg.get("role") == "customer" else "Agent"
            history_text += f"{role}: {msg.get('content', '')}\n"

    # Step 3: Build the prompt
    system_prompt = RAG_SYSTEM_PROMPT.format(context=context)

    user_prompt = ""
    if history_text:
        user_prompt += f"Conversation so far:\n{history_text}\n\n"
    user_prompt += f"Customer's latest question: {message}"

    # Step 4: Generate response
    llm = get_rag_llm()

    try:
        with track_llm_call(settings.llm_large_model, "rag_node", user_prompt) as tracker:
            response = await llm.ainvoke([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ])
            tracker["output_text"] = response.content

        return {
            "response": response.content,
            "sources": _extract_sources(context),
            "context_used": bool(context and "No relevant information" not in context),
        }
    except Exception as e:
        return {
            "response": "I apologize, but I'm experiencing a technical issue right now. Let me connect you with a human agent who can assist you immediately.",
            "sources": [],
            "context_used": False,
            "error": str(e),
        }


def _extract_sources(context: str) -> list[str]:
    """Extract source document names from the formatted context string."""
    sources = []
    for line in context.split("\n"):
        if line.startswith("--- Source:"):
            source_name = line.split("Source:")[1].split("(")[0].strip()
            if source_name not in sources:
                sources.append(source_name)
    return sources
