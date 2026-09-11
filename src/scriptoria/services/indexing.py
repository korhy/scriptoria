"""Indexation dans Elasticsearch.

Elasticsearch est un index **jetable** : tout ce qui vit ici doit pouvoir être
reconstruit depuis Postgres par `make reindex`. Ne jamais y stocker une donnée
qui n'existe pas en base.

Le mapping combine un champ texte analysé (BM25) et un `dense_vector`, ce qui
permet la recherche hybride sans second magasin.
"""

from typing import Any

from elasticsearch import AsyncElasticsearch

from scriptoria.services.chunking import Chunk


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


async def ensure_index(es: AsyncElasticsearch, index: str, embedding_dim: int) -> None:
    """Crée l'index s'il n'existe pas. Idempotent."""
    raise NotImplementedError("Création d'index — à implémenter")


async def index_chunks(
    es: AsyncElasticsearch,
    index: str,
    chunks: list[Chunk],
    embeddings: list[list[float]],
) -> int:
    """Indexe des fragments et leurs vecteurs. Retourne le nombre de documents écrits.

    Doit être idempotent : l'identifiant ES est `chunk_id`, si bien qu'une
    réindexation écrase au lieu de dupliquer.
    """
    raise NotImplementedError("Indexation — à implémenter")
