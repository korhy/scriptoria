"""Recherche hybride : lexicale (BM25) et sémantique (dense_vector).

**Licence — vérifié le 2026-09-11 sur Elasticsearch 9.1.0, licence `basic`.**
La fusion RRF native (`retriever: {rrf: ...}`) est **refusée** :

    security_exception: current license is non-compliant for
    [Reciprocal Rank Fusion (RRF)]

`reciprocal_rank_fusion` ci-dessous n'est donc pas un plan de secours, c'est le
chemin réel : la fusion se fait côté Python. Une vingtaine de lignes, aucune
contrainte de licence, résultat équivalent à nos volumes.

Conséquence sur l'implémentation d'`hybrid_search` : émettre **deux** requêtes
(une `match` BM25, une `knn`), puis fusionner leurs classements ici. Ne pas
essayer de passer par un `retriever` RRF, il sera rejeté à l'exécution.
"""

import logging
from dataclasses import dataclass, replace
from typing import Any

from elasticsearch import AsyncElasticsearch

logger = logging.getLogger(__name__)

# Chaque stratégie remonte plus de candidats que demandé : la fusion n'a d'effet
# que si les deux classements se recouvrent au-delà du seul sommet.
CANDIDATES_MULTIPLIER = 3
MIN_CANDIDATES = 10

# Le vecteur n'est jamais rapatrié : 1024 flottants par fragment pour rien.
SOURCE_FIELDS = ["document_id", "page_number", "content"]


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    page_number: int
    content: str
    score: float


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Fusionne plusieurs classements par RRF.

    Chaque document reçoit la somme de `1 / (k + rang)` sur les classements où il
    apparaît. Indépendant de l'échelle des scores — ce qui est exactement le
    problème quand on mélange BM25 et similarité cosinus.

    Args:
        rankings: un classement par stratégie, chacun ordonné du meilleur au pire.
        k: constante d'amortissement. 60 est la valeur usuelle de la littérature.

    Returns:
        Score fusionné par identifiant, non trié.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


def _collect(response: dict[str, Any], chunks: dict[str, RetrievedChunk]) -> list[str]:
    """Enregistre les fragments d'une réponse ES et rend leur classement."""
    ranking: list[str] = []
    for hit in response["hits"]["hits"]:
        chunk_id = hit["_id"]
        ranking.append(chunk_id)
        source = hit["_source"]
        chunks.setdefault(
            chunk_id,
            RetrievedChunk(
                chunk_id=chunk_id,
                document_id=source["document_id"],
                page_number=source["page_number"],
                content=source["content"],
                # Renseigné après fusion : un score BM25 et un cosinus n'ont
                # aucune échelle commune, seul le rang est comparable.
                score=0.0,
            ),
        )
    return ranking


async def hybrid_search(
    es: AsyncElasticsearch,
    index: str,
    query: str,
    query_embedding: list[float],
    top_k: int = 5,
) -> list[RetrievedChunk]:
    """Recherche hybride : BM25 et kNN, puis fusion des classements.

    **Deux requêtes**, et non un `retriever` RRF natif : celui-ci est refusé par
    la licence basic (vérifié le 2026-09-11 sur ES 9.1.0). La fusion se fait donc
    ici, en Python.

    Args:
        es: client Elasticsearch.
        index: index des fragments.
        query: question de l'utilisateur, telle qu'elle a été saisie.
        query_embedding: la même question vectorisée par le modèle d'embedding.
        top_k: nombre de fragments rendus après fusion.
    """
    candidates = max(top_k * CANDIDATES_MULTIPLIER, MIN_CANDIDATES)

    lexical = await es.search(
        index=index,
        query={"match": {"content": query}},
        size=candidates,
        source=SOURCE_FIELDS,
    )
    semantic = await es.search(
        index=index,
        knn={
            "field": "embedding",
            "query_vector": query_embedding,
            "k": candidates,
            "num_candidates": candidates * 5,
        },
        size=candidates,
        source=SOURCE_FIELDS,
    )

    chunks: dict[str, RetrievedChunk] = {}
    rankings = [_collect(lexical, chunks), _collect(semantic, chunks)]
    fused = reciprocal_rank_fusion(rankings)

    # Tri par score décroissant, puis par identifiant : à score égal, l'ordre
    # doit être reproductible d'une requête à l'autre.
    ordered = sorted(fused.items(), key=lambda item: (-item[1], item[0]))[:top_k]
    logger.info(
        "recherche « %s » : %s candidat(s) lexicaux, %s sémantiques, %s rendu(s)",
        query,
        len(rankings[0]),
        len(rankings[1]),
        len(ordered),
    )
    return [replace(chunks[chunk_id], score=score) for chunk_id, score in ordered]


def build_answer_context(chunks: list[RetrievedChunk]) -> dict[str, Any]:
    """Prépare le contexte transmis au LLM de génération.

    Chaque passage est numéroté et **rattaché à sa page** : une réponse dont on
    ne peut pas remonter à la page source est invérifiable, ce qui est
    inacceptable pour un fonds documentaire dont l'OCR peut se tromper.

    Fonction synchrone, contrairement au stub d'origine : elle ne fait que du
    formatage, et l'annoncer `async` laisserait croire à une I/O.
    """
    passages = [
        f"[{position}] page {chunk.page_number} du document {chunk.document_id}\n{chunk.content}"
        for position, chunk in enumerate(chunks, start=1)
    ]
    return {"context": "\n\n".join(passages), "sources": chunks}
