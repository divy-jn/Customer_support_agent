"""
Web Search MCP Server — exposes web search capabilities as MCP tools.

Allows agents to search the live internet when a customer's question
falls outside the knowledge base.

Usage:
    cd backend
    python -m app.mcp.web_search_server
"""

import os
import sys
import urllib.request
import urllib.parse
import json

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# ──────────────────────────────────────────────
#  MCP Server Setup
# ──────────────────────────────────────────────
mcp = FastMCP(
    "WebSearch",
    description="MCP server for searching the live web when knowledge base is insufficient.",
)


@mcp.tool()
def web_search(query: str, num_results: int = 5) -> str:
    """
    Search the web for information related to a customer query.
    Use this ONLY when the knowledge base does not contain the answer.

    Args:
        query: The search query string.
        num_results: Number of results to return (default 5, max 10).
    """
    num_results = min(num_results, 10)

    try:
        # Use DuckDuckGo Instant Answer API (free, no API key required)
        encoded_query = urllib.parse.quote(query)
        url = f"https://api.duckduckgo.com/?q={encoded_query}&format=json&no_html=1&skip_disambig=1"

        req = urllib.request.Request(url, headers={"User-Agent": "Adi/1.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        results = []

        # Abstract (main answer)
        if data.get("AbstractText"):
            results.append({
                "title": data.get("Heading", ""),
                "snippet": data["AbstractText"],
                "source": data.get("AbstractURL", ""),
                "type": "abstract",
            })

        # Related topics
        for topic in data.get("RelatedTopics", [])[:num_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "title": topic.get("Text", "")[:100],
                    "snippet": topic.get("Text", ""),
                    "source": topic.get("FirstURL", ""),
                    "type": "related",
                })

        if not results:
            return json.dumps({
                "message": f"No direct results found for '{query}'. The customer may need to be directed to a specific website or resource.",
                "suggestion": "Try rephrasing the search query or ask the customer for more specific details.",
            })

        return json.dumps(results[:num_results], indent=2)

    except Exception as e:
        return json.dumps({
            "error": f"Web search failed: {str(e)}",
            "suggestion": "Inform the customer that you are unable to search the web at this time and offer alternative assistance.",
        })


if __name__ == "__main__":
    mcp.run(transport="stdio")
