"""Indexation dans Elasticsearch.

Elasticsearch est un index **jetable** : tout ce qui vit ici doit pouvoir être
reconstruit depuis Postgres par `make reindex`. Ne jamais y stocker une donnée
qui n'existe pas en base.

Le mapping combine un champ texte analysé (BM25) et un `dense_vector`, ce qui
permet la recherche hybride sans second magasin.

L'identifiant ES d'un fragment est son `chunk_id`, dérivé de la position
(document, page) : une réindexation **écrase** au lieu de dupliquer. Sans cela,
`make reindex` gonflerait l'index à chaque passage et la même page remonterait
deux fois dans les résultats.
"""

import logging
from typing import Any

from elasticsearch import AsyncElasticsearch

from scriptoria.services.chunking import Chunk

logger = logging.getLogger(__name__)


class IndexingError(RuntimeError):
    """L'indexation n'a pas abouti, ou ses entrées sont incohérentes."""


def build_index_mapping(embedding_dim: int) -> dict[str, Any]:
    """Mapping de l'index : BM25 et vecteurs dans le même document."""
    return {
        "mappings": {
            "properties": {
                "document_id": {"type": "keyword"},
                "page_number": {"type": "integer"},
                "content": {
                    "type": "text",
                    # Analyseur français : les documents sources sont en français.
                    "analyzer": "french",
                },
                "embedding": {
                    "type": "dense_vector",
                    "dims": embedding_dim,
                    "index": True,
                    "similarity": "cosine",
                },
            }
        }
    }


def validate_embedding_dim(embeddings: list[list[float]], expected: int) -> None:
    """Vérifie la dimension des vecteurs avant de les soumettre à Elasticsearch.

    ES rejette une dimension non conforme, mais son message porte sur le mapping
    et non sur le modèle : échouer ici permet de nommer la vraie cause — un
    changement de modèle d'embedding sans réindexation.

    Raises:
        IndexingError: si un vecteur n'a pas la dimension attendue.
    """
    for vector in embeddings:
        if len(vector) != expected:
            raise IndexingError(
                f"dimension {len(vector)} alors que l'index attend {expected} : "
                "le modèle d'embedding a-t-il changé ? Un `make reindex` complet "
                "est alors nécessaire, avec un index recréé."
            )


async def ensure_index(es: AsyncElasticsearch, index: str, embedding_dim: int) -> None:
    """Crée l'index s'il n'existe pas. Idempotent."""
    if await es.indices.exists(index=index):
        return

    await es.indices.create(index=index, **build_index_mapping(embedding_dim))
    logger.info("index %s créé (dense_vector %s dimensions)", index, embedding_dim)


async def index_chunks(
    es: AsyncElasticsearch,
    index: str,
    chunks: list[Chunk],
    embeddings: list[list[float]],
) -> int:
    """Indexe des fragments et leurs vecteurs. Retourne le nombre de documents écrits.

    Doit être idempotent : l'identifiant ES est `chunk_id`, si bien qu'une
    réindexation écrase au lieu de dupliquer.

    Raises:
        IndexingError: appariement fragments/vecteurs incohérent, ou refus d'ES.
    """
    if len(chunks) != len(embeddings):
        raise IndexingError(
            f"{len(chunks)} fragment(s) pour {len(embeddings)} vecteur(s) : "
            "chaque fragment serait indexé avec le vecteur d'un autre."
        )
    if not chunks:
        return 0

    operations: list[dict[str, Any]] = []
    for chunk, embedding in zip(chunks, embeddings, strict=True):
        operations.append({"index": {"_index": index, "_id": chunk.chunk_id}})
        operations.append(
            {
                # `str` et non UUID : le champ est un `keyword` côté ES, et le
                # sérialiseur JSON ne connaît pas UUID.
                "document_id": str(chunk.document_id),
                "page_number": chunk.page_number,
                "content": chunk.content,
                "embedding": embedding,
            }
        )

    response = await es.bulk(operations=operations, refresh=True)
    if response.get("errors"):
        # ES répond 200 même quand des lignes échouent : ne pas lire `errors`
        # laisserait un index incomplet passer pour complet.
        raisons = [
            item.get("index", {}).get("error", {}).get("reason", "?")
            for item in response.get("items", [])
            if item.get("index", {}).get("error")
        ]
        raise IndexingError(f"Elasticsearch a refusé {len(raisons)} fragment(s) : {raisons[:3]}")

    logger.info("%s fragment(s) indexé(s) dans %s", len(chunks), index)
    return len(chunks)
