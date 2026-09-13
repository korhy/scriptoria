"""Interrogation du RAG : recherche hybride et réponse générée.

Deux étapes, deux modèles : la question est vectorisée par le modèle
d'embedding, puis — pour `/answer` seulement — la réponse est rédigée par le
modèle de génération. Sur une machine à 16 Go avec `OLLAMA_MAX_LOADED_MODELS=1`,
cela implique un échange de modèle : les premières secondes d'un `/search/answer`
sont du chargement, pas de l'inférence. D'où le soin à ne **pas** appeler la
génération quand aucun passage n'a été trouvé.

Une panne n'est jamais rendue comme une absence de résultat : Ollama éteint ou
index absent donnent 503 avec la cause, pas une liste vide qui laisserait croire
que le fonds documentaire ne contient rien.
"""

import logging

from elasticsearch import NotFoundError
from fastapi import APIRouter, HTTPException, status

from scriptoria.api.deps import AppSettings, EsClient, OllamaClient
from scriptoria.config import Settings
from scriptoria.schemas.search import AnswerResponse, SearchHit, SearchQuery, SearchResponse
from scriptoria.services.embeddings import EmbeddingError, embed_texts
from scriptoria.services.generation import GenerationError, context_budget, generate_answer
from scriptoria.services.retrieval import RetrievedChunk, build_answer_context, hybrid_search

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])

NO_PASSAGE_ANSWER = (
    "Aucun passage indexé ne répond à cette question. Le document concerné "
    "a-t-il été validé puis indexé ?"
)


def _to_hit(chunk: RetrievedChunk) -> SearchHit:
    return SearchHit(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        page_number=chunk.page_number,
        content=chunk.content,
        score=chunk.score,
    )


async def _retrieve(
    payload: SearchQuery,
    es: EsClient,
    ollama: OllamaClient,
    settings: Settings,
) -> list[RetrievedChunk]:
    """Vectorise la question, puis cherche. Traduit les pannes en 503.

    La même question part deux fois vers Elasticsearch : en texte pour BM25, en
    vecteur pour le kNN. La fusion RRF se fait côté Python — le `retriever` natif
    est refusé par la licence basic.
    """
    try:
        vectors = await embed_texts(ollama, [payload.query], settings.ollama_embedding_model)
    except EmbeddingError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    try:
        return await hybrid_search(
            es,
            settings.elasticsearch_index,
            payload.query,
            vectors[0],
            top_k=payload.top_k,
        )
    except NotFoundError as exc:
        # Index absent : ce n'est pas « aucun résultat », c'est une stack
        # incomplète. Le dire, et dire quoi lancer.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"index '{settings.elasticsearch_index}' absent : lancer `make reindex` "
            "pour le reconstruire depuis Postgres.",
        ) from exc


@router.post("", response_model=SearchResponse, summary="Recherche hybride")
async def search(
    payload: SearchQuery,
    es: EsClient,
    ollama: OllamaClient,
    settings: AppSettings,
) -> SearchResponse:
    chunks = await _retrieve(payload, es, ollama, settings)
    return SearchResponse(hits=[_to_hit(chunk) for chunk in chunks])


@router.post("/answer", response_model=AnswerResponse, summary="Réponse générée")
async def answer(
    payload: SearchQuery,
    es: EsClient,
    ollama: OllamaClient,
    settings: AppSettings,
) -> AnswerResponse:
    """Rédige une réponse adossée aux passages retrouvés, et les rend avec elle.

    Les sources ne sont pas un ornement : une transcription automatique peut
    comporter des erreurs de lecture, et une réponse dont on ne peut pas
    remonter à la page est invérifiable.
    """
    chunks = await _retrieve(payload, es, ollama, settings)
    if not chunks:
        # Aucun passage : ne pas charger le modèle de génération pour n'avoir
        # rien à dire. Sur 16 Go, cet échange de modèle coûte des secondes.
        logger.info("aucun passage pour « %s » — génération évitée", payload.query)
        return AnswerResponse(answer=NO_PASSAGE_ANSWER, sources=[])

    num_ctx = settings.ollama_generation_num_ctx
    num_predict = settings.ollama_generation_num_predict
    context = build_answer_context(
        chunks, max_chars=context_budget(payload.query, num_ctx, num_predict)
    )
    try:
        generated = await generate_answer(
            ollama,
            payload.query,
            context["context"],
            settings.ollama_generation_model,
            num_ctx=num_ctx,
            num_predict=num_predict,
        )
    except GenerationError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    # Seuls les passages transmis au modèle : une page qu'il n'a pas lue ne peut
    # pas fonder sa réponse.
    return AnswerResponse(
        answer=generated, sources=[_to_hit(chunk) for chunk in context["sources"]]
    )
