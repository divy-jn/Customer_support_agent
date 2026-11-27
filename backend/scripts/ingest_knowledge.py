"""
Ingest knowledge base documents into Pinecone for RAG retrieval.

Reads all .md files from the knowledge_base/ directory, splits them into
chunks, generates embeddings using Pinecone's Inference API, and stores them
in a Pinecone index.

Usage:
    cd backend
    python -m scripts.ingest_knowledge
"""

import codecs
import os
import sys

from pinecone import Pinecone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.config import settings

# ──────────────────────────────────────────────
#  Configuration
# ──────────────────────────────────────────────
EMBEDDING_MODEL = "llama-text-embed-v2"
CHUNK_SIZE = 500          # characters per chunk
CHUNK_OVERLAP = 100       # overlap between chunks
BATCH_SIZE = 50


def split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks by character count, respecting paragraph boundaries."""
    if not 0 <= overlap < chunk_size:
        raise ValueError("Require 0 <= overlap < chunk_size")
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(current_chunk) + len(para) + 2 <= chunk_size:
            current_chunk += ("\n\n" + para) if current_chunk else para
        else:
            if current_chunk:
                chunks.append(current_chunk)
            if len(para) > chunk_size:
                words = para.split()
                current_chunk = ""
                for word in words:
                    if len(current_chunk) + len(word) + 1 <= chunk_size:
                        current_chunk += (" " + word) if current_chunk else word
                    else:
                        chunks.append(current_chunk)
                        overlap_text = current_chunk[-overlap:] if overlap else ""
                        current_chunk = overlap_text + " " + word
            else:
                if chunks:
                    overlap_text = chunks[-1][-overlap:] if overlap else ""
                    current_chunk = overlap_text + "\n\n" + para
                else:
                    current_chunk = para

    if current_chunk.strip():
        chunks.append(current_chunk)

    bounded = []
    for chunk in chunks:
        chunk = chunk.strip()
        start = 0
        while start < len(chunk):
            part = chunk[start:start + chunk_size].strip()
            if part:
                bounded.append(part)
            if start + chunk_size >= len(chunk):
                break
            start += chunk_size - overlap
    return bounded


def iter_file_chunks(path, byte_limit: int):
    """Read bounded UTF-8 blocks and yield overlapping, bounded text chunks."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""
    bytes_read = 0
    with path.open("rb") as stream:
        while block := stream.read(min(4096, byte_limit - bytes_read + 1)):
            bytes_read += len(block)
            if bytes_read > byte_limit:
                raise ValueError(f"{path.name} exceeds MAX_KB_FILE_BYTES ({byte_limit} bytes)")
            buffer += decoder.decode(block)
            while len(buffer) > CHUNK_SIZE:
                split = buffer.rfind("\n\n", CHUNK_SIZE // 2, CHUNK_SIZE)
                if split < 0:
                    split = buffer.rfind(" ", CHUNK_SIZE // 2, CHUNK_SIZE)
                end = split if split >= 0 else CHUNK_SIZE
                if chunk := buffer[:end].strip():
                    yield chunk
                buffer = buffer[end - CHUNK_OVERLAP:]
    buffer += decoder.decode(b"", final=True)
    if chunk := buffer.strip():
        yield chunk


def ingest():
    """Embed and upsert one bounded batch at a time."""
    kb_dir = settings.knowledge_base_path
    md_files = sorted(path for path in kb_dir.glob("*.md") if path.is_file())
    byte_limit = settings.max_kb_file_bytes
    for path in md_files:
        if path.stat().st_size > byte_limit:
            raise ValueError(f"{path.name} exceeds MAX_KB_FILE_BYTES ({byte_limit} bytes)")

    pc = index = None
    total_chunks = 0
    batch = []

    def flush():
        nonlocal pc, index, total_chunks
        if pc is None:
            pc = Pinecone(api_key=settings.pinecone_api_key)
            index = pc.Index(settings.pinecone_index_name)
        embeddings = pc.inference.embed(
            model=EMBEDDING_MODEL,
            inputs=[entry["text"] for entry in batch],
            parameters={"input_type": "passage", "truncate": "END"},
        )
        index.upsert(vectors=[
            {"id": entry["id"], "values": embedding.values, "metadata": entry["metadata"]}
            for entry, embedding in zip(batch, embeddings, strict=True)
        ])
        total_chunks += len(batch)
        batch.clear()

    for path in md_files:
        # Count this file without retaining its chunks, preserving vector metadata.
        count = sum(1 for _ in iter_file_chunks(path, byte_limit))
        for i, chunk in enumerate(iter_file_chunks(path, byte_limit)):
            batch.append({
                "id": f"{path.name}__chunk_{i:03d}",
                "text": chunk,
                "metadata": {
                    "text": chunk,
                    "source": path.name,
                    "document_name": path.stem.replace("_", " ").title(),
                    "chunk_index": i,
                    "total_chunks": count,
                },
            })
            if len(batch) == BATCH_SIZE:
                flush()

    if batch:
        flush()
    if not total_chunks:
        raise FileNotFoundError(f"No ingestible markdown documents in {kb_dir}")
    print(f"Ingested {total_chunks} knowledge base chunks.")


if __name__ == "__main__":
    ingest()
