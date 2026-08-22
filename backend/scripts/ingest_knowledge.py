"""
Ingest knowledge base documents into Pinecone for RAG retrieval.

Reads all .md files from the knowledge_base/ directory, splits them into
chunks, generates embeddings using Pinecone's Inference API, and stores them
in a Pinecone index.

Usage:
    cd backend
    python -m scripts.ingest_knowledge
"""

import os
import sys
import glob

from pinecone import Pinecone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.config import settings

# ──────────────────────────────────────────────
#  Configuration
# ──────────────────────────────────────────────
EMBEDDING_MODEL = "llama-text-embed-v2"
CHUNK_SIZE = 500          # characters per chunk
CHUNK_OVERLAP = 100       # overlap between chunks


def split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks by character count, respecting paragraph boundaries."""
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
                        overlap_text = current_chunk[-overlap:] if len(current_chunk) > overlap else current_chunk
                        current_chunk = overlap_text + " " + word
            else:
                if chunks:
                    overlap_text = chunks[-1][-overlap:] if len(chunks[-1]) > overlap else chunks[-1]
                    current_chunk = overlap_text + "\n\n" + para
                else:
                    current_chunk = para

    if current_chunk.strip():
        chunks.append(current_chunk)

    return chunks


def ingest():
    """Main ingestion function."""
    kb_dir = os.path.join(os.path.dirname(__file__), "..", "knowledge_base")
    kb_dir = os.path.abspath(kb_dir)

    md_files = glob.glob(os.path.join(kb_dir, "*.md"))
    if not md_files:
        print(f"❌ No .md files found in {kb_dir}")
        return

    print(f"📚 Found {len(md_files)} knowledge base documents:")
    for f in md_files:
        print(f"   - {os.path.basename(f)}")

    print(f"\n🔧 Connecting to Pinecone...")
    pc = Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(settings.pinecone_index_name)
    print(f"   ✅ Connected to index: {settings.pinecone_index_name}")

    # Process each document
    total_chunks = 0
    all_records = []

    for filepath in md_files:
        filename = os.path.basename(filepath)
        doc_name = filename.replace(".md", "").replace("_", " ").title()

        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        chunks = split_text(content)
        print(f"\n📄 {filename}: {len(chunks)} chunks")

        for i, chunk in enumerate(chunks):
            chunk_id = f"{filename}__chunk_{i:03d}"
            
            # Store raw text and metadata
            metadata = {
                "text": chunk,
                "source": filename,
                "document_name": doc_name,
                "chunk_index": i,
                "total_chunks": len(chunks),
            }
            
            all_records.append({
                "id": chunk_id,
                "text": chunk,
                "metadata": metadata
            })
            total_chunks += 1

    # Batch embed and insert into Pinecone
    print(f"\n⬆️  Embedding and inserting {total_chunks} chunks into Pinecone...")
    batch_size = 50
    for i in range(0, len(all_records), batch_size):
        end = min(i + batch_size, len(all_records))
        batch = all_records[i:end]
        texts = [record["text"] for record in batch]
        
        # Get embeddings
        embedding_response = pc.inference.embed(
            model=EMBEDDING_MODEL,
            inputs=texts,
            parameters={"input_type": "passage", "truncate": "END"}
        )
        
        # Upsert
        vectors = []
        for record, embed_data in zip(batch, embedding_response):
            vectors.append({
                "id": record["id"],
                "values": embed_data.values,
                "metadata": record["metadata"]
            })
            
        index.upsert(vectors=vectors)
        print(f"   ✅ Upserted batch {i} to {end}")

    print(f"\n{'=' * 50}")
    print(f"🎉 KNOWLEDGE BASE INGESTION COMPLETE!")
    print(f"   Documents processed: {len(md_files)}")
    print(f"   Total chunks:       {total_chunks}")
    print(f"   Embedding model:    {EMBEDDING_MODEL}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    ingest()
