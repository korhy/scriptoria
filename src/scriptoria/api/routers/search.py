"""Interrogation du RAG."""

from fastapi import APIRouter, HTTPException, status

from scriptoria.schemas.search import SearchQuery

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", status_code=status.HTTP_501_NOT_IMPLEMENTED, summary="Recherche hybride")
async def search(payload: SearchQuery) -> None:
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "Recherche non implémentée : voir services/retrieval.py.",
    )


@router.post("/answer", status_code=status.HTTP_501_NOT_IMPLEMENTED, summary="Réponse générée")
async def answer(payload: SearchQuery) -> None:
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "Génération non implémentée : recherche hybride puis LLM local via Ollama.",
    )
