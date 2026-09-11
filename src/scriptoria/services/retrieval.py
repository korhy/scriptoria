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

from dataclasses import dataclass
from typing import Any

from elasticsearch import AsyncElasticsearch


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


async def hybrid_search(
    es: AsyncElasticsearch,
    index: str,
    query: str,
    query_embedding: list[float],
    top_k: int = 5,
) -> list[RetrievedChunk]:
    """Recherche hybride : BM25 et kNN, puis fusion des classements."""
    raise NotImplementedError("Recherche hybride — à implémenter")


async def build_answer_context(chunks: list[RetrievedChunk]) -> dict[str, Any]:
    """Prépare le contexte transmis au LLM de génération."""
    raise NotImplementedError("Contexte de génération — à implémenter")
