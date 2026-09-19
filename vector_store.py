"""
Vector Store Module — ChromaDB + Gemini Embeddings

Manages all policy document storage, chunking, embedding, and semantic
retrieval for the Acme Corp HR Portal. Uses a persistent local ChromaDB
collection and Google's gemini-embedding-2 model for embeddings.
"""

import os
import re
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings
from google import genai

BASE_DIR = Path(__file__).parent.resolve()
CHROMA_DIR = BASE_DIR / "chroma_db"

COLLECTION_NAME = "hr_policies"
EMBEDDING_MODEL = "gemini-embedding-2"


class VectorStore:
    """
    Encapsulates ChromaDB-backed vector storage for HR policy documents.

    Each document is split into markdown section chunks (by ## headers).
    Every chunk is embedded via Gemini and stored with metadata:
      - doc_name: slug of the source document (e.g. "leave_policy")
      - filename: full filename (e.g. "leave_policy.md")
      - header:   the section heading text
      - chunk_index: ordinal position of the chunk within the document
    """

    def __init__(self):
        self._client: Optional[chromadb.PersistentClient] = None
        self._collection = None
        self._genai_client: Optional[genai.Client] = None

    def _ensure_initialized(self):
        """Lazy-initialize ChromaDB client and Gemini client."""
        if self._collection is not None:
            return

        CHROMA_DIR.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        try:
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            self._collection.count()
        except Exception as error:
            if "Nothing found on disk" not in str(error):
                raise
            self._client.delete_collection(name=COLLECTION_NAME)
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )

    def _embed(self, text: str) -> list[float]:
        """Generate a single embedding vector for the given text."""
        self._ensure_initialized()
        if self._genai_client is None:
            api_key = os.getenv("GEMINI_API_KEY", "")
            if not api_key or "paste_your_gemini_api_key_here" in api_key:
                raise RuntimeError("GEMINI_API_KEY is not configured.")
            self._genai_client = genai.Client(api_key=api_key)
        response = self._genai_client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
        )
        return response.embeddings[0].values

    def _chunk_document(self, doc_name: str, filename: str, content: str) -> list[dict]:
        """
        Split a markdown document into chunks by ## section headers.
        Falls back to 1000-character windows if no headers are found.
        Returns a list of chunk dicts ready for upsert.
        """
        chunks = []

        # Split on markdown section boundaries (## or ###)
        raw_sections = re.split(r"\n(?=#{1,3}\s)", content.strip())

        # Filter out tiny fragments (whitespace-only or < 30 chars)
        sections = [s.strip() for s in raw_sections if len(s.strip()) >= 30]

        if not sections:
            # Fallback: split by 1000-char windows
            for i in range(0, len(content), 1000):
                chunk_text = content[i : i + 1000].strip()
                if chunk_text:
                    sections.append(chunk_text)

        for idx, section_text in enumerate(sections):
            first_line = section_text.split("\n")[0]
            header = re.sub(r"^#+\s*", "", first_line).strip() or f"Section {idx + 1}"

            chunk_id = f"{doc_name}__chunk_{idx}"
            chunks.append(
                {
                    "id": chunk_id,
                    "text": section_text,
                    "metadata": {
                        "doc_name": doc_name,
                        "filename": filename,
                        "header": header,
                        "chunk_index": idx,
                    },
                }
            )

        return chunks

    def ingest_document(self, filename: str, content: str) -> dict:
        """
        Ingest a policy document into ChromaDB.

        Steps:
          1. Derive doc_name slug from filename.
          2. Remove any previously stored chunks for this doc.
          3. Chunk the document.
          4. Embed each chunk with Gemini.
          5. Upsert all chunks into ChromaDB.

        Returns a summary dict with chunk count.
        """
        self._ensure_initialized()

        # Derive slug: strip extension, lowercase, replace spaces/dashes with _
        doc_name = Path(filename).stem.lower().replace(" ", "_").replace("-", "_")
        clean_filename = filename if "." in filename else f"{filename}.md"

        chunks = self._chunk_document(doc_name, clean_filename, content)
        if not chunks:
            return {"doc_name": doc_name, "chunks_added": 0, "status": "no_content"}

        self._delete_document_chunks(doc_name)

        ids = []
        documents = []
        embeddings = []
        metadatas = []

        for chunk in chunks:
            emb = self._embed(chunk["text"])
            ids.append(chunk["id"])
            documents.append(chunk["text"])
            embeddings.append(emb)
            metadatas.append(chunk["metadata"])

        self._collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        return {
            "doc_name": doc_name,
            "filename": clean_filename,
            "chunks_added": len(chunks),
            "status": "success",
        }

    def _delete_document_chunks(self, doc_name: str):
        """Delete all chunks for a given doc_name slug from the collection."""
        self._ensure_initialized()
        existing = self._collection.get(
            where={"doc_name": {"$eq": doc_name}},
            include=[],
        )
        ids_to_delete = existing.get("ids", [])
        if ids_to_delete:
            self._collection.delete(ids=ids_to_delete)

    def delete_document(self, doc_name: str) -> dict:
        """
        Public method to delete a document by name slug.
        Returns number of chunks removed.
        """
        self._ensure_initialized()
        existing = self._collection.get(
            where={"doc_name": {"$eq": doc_name}},
            include=[],
        )
        ids_to_delete = existing.get("ids", [])
        if ids_to_delete:
            self._collection.delete(ids=ids_to_delete)
        return {"doc_name": doc_name, "chunks_removed": len(ids_to_delete)}

    def semantic_search(self, query: str, n_results: int = 5) -> list[dict]:
        """
        Embed the query and retrieve the top-N most relevant chunks.

        Returns a list of dicts:
          { doc_name, filename, header, content, distance }
        sorted by ascending cosine distance (most similar first).
        """
        self._ensure_initialized()

        # Guard: empty collection
        total = self._collection.count()
        if total == 0:
            return []

        query_embedding = self._embed(query)
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=min(n_results, total),
            include=["documents", "metadatas", "distances"],
        )

        hits = []
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0]

        for doc_text, meta, dist in zip(docs, metas, dists):
            hits.append(
                {
                    "doc_name": meta.get("doc_name", "unknown"),
                    "filename": meta.get("filename", "unknown"),
                    "header": meta.get("header", ""),
                    "content": doc_text,
                    "distance": round(dist, 4),
                }
            )

        return hits

    def get_document(self, doc_name: str) -> list[dict]:
        """Return all indexed chunks for a document in source order."""
        self._ensure_initialized()
        results = self._collection.get(
            where={"doc_name": {"$eq": doc_name}},
            include=["documents", "metadatas"],
        )

        documents = results.get("documents", [])
        metadatas = results.get("metadatas", [])
        chunks = []
        for content, metadata in zip(documents, metadatas):
            chunks.append(
                {
                    "doc_name": metadata.get("doc_name", doc_name),
                    "filename": metadata.get("filename", "unknown"),
                    "header": metadata.get("header", ""),
                    "chunk_index": metadata.get("chunk_index", 0),
                    "content": content,
                }
            )

        return sorted(chunks, key=lambda chunk: chunk["chunk_index"])

    def list_ingested_documents(self) -> list[dict]:
        """
        Return a deduplicated list of documents currently in the vector store.
        Each entry: { doc_name, filename, chunk_count }
        """
        self._ensure_initialized()
        all_items = self._collection.get(include=["metadatas"])
        metas = all_items.get("metadatas", [])

        doc_map: dict[str, dict] = {}
        for m in metas:
            dn = m.get("doc_name", "unknown")
            fn = m.get("filename", "unknown")
            if dn not in doc_map:
                doc_map[dn] = {"doc_name": dn, "filename": fn, "chunk_count": 0}
            doc_map[dn]["chunk_count"] += 1

        return sorted(doc_map.values(), key=lambda x: x["doc_name"])

    def collection_stats(self) -> dict:
        """Return total chunk count and per-document summary."""
        self._ensure_initialized()
        docs = self.list_ingested_documents()
        return {
            "total_chunks": self._collection.count(),
            "document_count": len(docs),
            "documents": docs,
        }


# Singleton instance shared by the app and MCP server
vector_store = VectorStore()

