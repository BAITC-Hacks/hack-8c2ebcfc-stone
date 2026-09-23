"""ChromaDB helpers for document storage and semantic search."""

from __future__ import annotations

import chromadb

from backend.config import settings


COLLECTION_NAME = "docs"


def get_client(path: str | None = None) -> chromadb.PersistentClient:
    """Create a persistent Chroma client for local document indexes."""

    return chromadb.PersistentClient(path=path or settings.chroma_path)


def get_collection(name: str = COLLECTION_NAME):
    """Return the default document collection."""

    client = get_client()
    return client.get_or_create_collection(name)


def add_document(document_id: str, text: str, metadata: dict | None = None) -> None:
    """Store a document in Chroma for later search."""

    collection = get_collection()
    collection.add(
        ids=[document_id],
        documents=[text],
        metadatas=[metadata or {}],
    )


def search_documents(query: str, limit: int = 5) -> dict:
    """Search stored documents and return raw Chroma results."""

    collection = get_collection()
    return collection.query(query_texts=[query], n_results=limit)
