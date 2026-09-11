"""Schémas d'interrogation du RAG."""

from uuid import UUID

from pydantic import BaseModel, Field


class SearchQuery(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)


class SearchHit(BaseModel):
    chunk_id: str
    document_id: UUID
    page_number: int
    content: str
    score: float


class SearchResponse(BaseModel):
    hits: list[SearchHit]


class AnswerResponse(BaseModel):
    """Réponse générée, accompagnée des passages qui l'ont produite."""

    answer: str
    sources: list[SearchHit]
