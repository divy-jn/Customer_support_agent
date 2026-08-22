"""
File System MCP Server — exposes knowledge base document operations as MCP tools.

Allows agents to read, list, and search knowledge base documents directly
from the file system, enabling live policy updates without re-ingesting.

Usage:
    cd backend
    python -m app.mcp.filesystem_server
"""

import os
import sys
import glob

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from app.config import settings

# ──────────────────────────────────────────────
#  MCP Server Setup
# ──────────────────────────────────────────────
mcp = FastMCP(
    "KnowledgeBaseFS",
    description="MCP server for reading and searching knowledge base documents.",
)

KB_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "knowledge_base")
)


@mcp.tool()
def list_documents() -> str:
    """
    List all available knowledge base documents.
    Returns the filename and size of each document.
    """
    md_files = glob.glob(os.path.join(KB_DIR, "*.md"))
    docs = []
    for f in md_files:
        docs.append({
            "filename": os.path.basename(f),
            "title": os.path.basename(f).replace(".md", "").replace("_", " ").title(),
            "size_bytes": os.path.getsize(f),
        })
    return str(docs)


@mcp.tool()
def read_document(filename: str) -> str:
    """
    Read the full contents of a knowledge base document.

    Args:
        filename: The filename of the document (e.g., 'return_policy.md').
    """
    filepath = os.path.join(KB_DIR, filename)
    if not os.path.exists(filepath):
        return f"Error: Document '{filename}' not found. Use list_documents() to see available files."
    if not filepath.startswith(KB_DIR):
        return "Error: Access denied. Cannot read files outside the knowledge base directory."

    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


@mcp.tool()
def search_documents(keyword: str) -> str:
    """
    Search across all knowledge base documents for a keyword or phrase.
    Returns matching lines with their source document.

    Args:
        keyword: The keyword or phrase to search for (case-insensitive).
    """
    md_files = glob.glob(os.path.join(KB_DIR, "*.md"))
    matches = []

    for filepath in md_files:
        filename = os.path.basename(filepath)
        with open(filepath, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                if keyword.lower() in line.lower():
                    matches.append({
                        "source": filename,
                        "line": line_num,
                        "content": line.strip(),
                    })

    if not matches:
        return f"No matches found for '{keyword}' in any knowledge base document."

    return str(matches[:20])  # Limit to 20 results


if __name__ == "__main__":
    mcp.run(transport="stdio")
