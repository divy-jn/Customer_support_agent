"""
RAG retriever: queries Pinecone to find relevant knowledge base chunks.
"""

from pinecone import Pinecone

from app.config import settings


# ──────────────────────────────────────────────
#  Singleton Pinecone client + index
# ──────────────────────────────────────────────
_pc = None
_index = None


def _get_index():
    """Lazily initialize and return the Pinecone index."""
    global _pc, _index

    if _pc is None:
        _pc = Pinecone(api_key=settings.pinecone_api_key)

    if _index is None:
        _index = _pc.Index(settings.pinecone_index_name)

    return _pc, _index


def retrieve(query: str, top_k: int = 5) -> list[dict]:
    """
    Retrieve the top-k most relevant knowledge base chunks for a query.

    Returns a list of dicts, each containing:
        - text: the chunk content
        - source: the source filename
        - document_name: human-readable document name
        - score: cosine similarity score (lower distance = more relevant)
    """
    pc, index = _get_index()
    
    # Use Pinecone's free Inference API for Integrated Embeddings
    embedding_response = pc.inference.embed(
        model="llama-text-embed-v2",
        inputs=[query],
        parameters={"input_type": "query"}
    )
    query_vector = embedding_response[0].values

    results = index.query(
        vector=query_vector,
        top_k=top_k,
        include_metadata=True
    )

    retrieved = []
    if results and hasattr(results, "matches"):
        for match in results.matches:
            meta = match.metadata or {}
            retrieved.append({
                "text": meta.get("text", ""),
                "source": meta.get("source", "unknown"),
                "document_name": meta.get("document_name", "Unknown"),
                "score": round(match.score, 4),
            })

    return retrieved


def retrieve_as_context(query: str, top_k: int = 5) -> str:
    """
    Retrieve relevant chunks and format them as a context string
    suitable for injection into an LLM prompt.
    """
    results = retrieve(query, top_k=top_k)

    if not results:
        return "No relevant information found in the knowledge base."

    context_parts = []
    for i, r in enumerate(results, 1):
        context_parts.append(
            f"--- Source: {r['document_name']} (Relevance: {r['score']}) ---\n"
            f"{r['text']}\n"
        )

    return "\n".join(context_parts)
