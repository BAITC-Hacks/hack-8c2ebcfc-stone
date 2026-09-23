"""FastAPI entrypoint for the Stone backend."""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from backend.config import settings
from backend.db import add_transaction, init_db, list_transactions
from backend.vectordb import add_document, search_documents


app = FastAPI(
    title=settings.app_name,
    description="Backend API for the Stone hackathon AI assistant.",
    version="0.1.0",
)


class TransactionCreate(BaseModel):
    """Payload for creating a finance transaction demo record."""

    amount: float = Field(..., description="Transaction amount")
    category: str = Field(..., description="Transaction category")
    date: str = Field(..., description="Transaction date in YYYY-MM-DD format")
    comment: str = Field(default="", description="Optional note")


class DocumentCreate(BaseModel):
    """Payload for adding text to the local vector store."""

    document_id: str = Field(..., description="Stable document id")
    text: str = Field(..., description="Document text")


class SearchRequest(BaseModel):
    """Payload for semantic search over uploaded documents."""

    query: str = Field(..., description="Search query")
    limit: int = Field(default=5, ge=1, le=20, description="Maximum number of results")


@app.on_event("startup")
def startup() -> None:
    """Prepare local storage before the API starts serving requests."""

    init_db()


@app.get("/")
def root() -> dict:
    """Basic service metadata for smoke tests."""

    return {"name": settings.app_name, "status": "ok"}


@app.get("/health")
def health() -> dict:
    """Health endpoint for local checks and future CI."""

    return {"status": "healthy"}


@app.get("/transactions")
def get_transactions() -> list[dict]:
    """List demo transactions from SQLite."""

    return list_transactions()


@app.post("/transactions")
def create_transaction(payload: TransactionCreate) -> dict:
    """Create a demo transaction record."""

    return add_transaction(
        amount=payload.amount,
        category=payload.category,
        date=payload.date,
        comment=payload.comment,
    )


@app.post("/documents")
def create_document(payload: DocumentCreate) -> dict:
    """Add a document to the local vector store."""

    add_document(document_id=payload.document_id, text=payload.text)
    return {"status": "stored", "document_id": payload.document_id}


@app.post("/search")
def search(payload: SearchRequest) -> dict:
    """Run semantic search over stored documents."""

    return search_documents(query=payload.query, limit=payload.limit)
