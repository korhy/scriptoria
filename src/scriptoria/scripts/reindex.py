"""Reconstruit intégralement l'index Elasticsearch depuis Postgres.

Ce script est la garantie opérationnelle qu'Elasticsearch reste jetable. Tant
qu'il fonctionne, perdre le volume ES n'est pas un incident : on rejoue.

L'index est **supprimé puis recréé**. Reconstruire par-dessus l'existant
laisserait survivre les fragments de pages supprimées depuis, et l'index ne
serait plus le reflet de la base — ce qui est précisément ce qu'il doit être.
Destructif du seul côté ES, par construction reconstructible.

Passe par la tâche `index_document` du worker plutôt que de refaire son travail :
une seule implémentation de l'indexation, donc un seul comportement à tester.

Lancé par `make reindex`.
"""

import asyncio
import logging
import sys

import httpx
from elasticsearch import AsyncElasticsearch
from sqlalchemy import select

from scriptoria.config import get_settings
from scriptoria.db.models import Document
from scriptoria.db.session import create_engine, create_sessionmaker
from scriptoria.domain.enums import DocumentStatus
from scriptoria.services.indexing import ensure_index
from scriptoria.workers.tasks import index_document

logger = logging.getLogger(__name__)

# Seuls ces états ont des transcriptions validées. Réindexer un document non relu
# remettrait du texte non vérifié dans l'index.
REINDEXABLE_STATUSES = (DocumentStatus.VALIDATED, DocumentStatus.INDEXED)


async def reindex_all() -> int:
    """Réindexe toutes les transcriptions validées. Retourne le nombre de fragments."""
    settings = get_settings()
    engine = create_engine(settings)
    es = AsyncElasticsearch(settings.elasticsearch_url)
    ollama = httpx.AsyncClient(
        base_url=settings.ollama_base_url, timeout=settings.ollama_timeout_seconds
    )
    factory = create_sessionmaker(engine)
    ctx = {"settings": settings, "sessionmaker": factory, "es": es, "ollama": ollama}
    total = 0

    try:
        await es.indices.delete(index=settings.elasticsearch_index, ignore_unavailable=True)
        await ensure_index(es, settings.elasticsearch_index, settings.embedding_dim)

        async with factory() as session:
            result = await session.execute(
                select(Document.id)
                .where(Document.status.in_(REINDEXABLE_STATUSES))
                .order_by(Document.created_at)
            )
            document_ids = list(result.scalars().all())

        logger.info("%s document(s) à réindexer", len(document_ids))
        for document_id in document_ids:
            total += await index_document(ctx, str(document_id))
    finally:
        # Refermer même en échec : un script qui laisse des connexions ouvertes
        # fait traîner la VM Docker, déjà limitée à 8 Go.
        await ollama.aclose()
        await es.close()
        await engine.dispose()

    return total


def main() -> int:
    logging.basicConfig(level=get_settings().log_level)
    try:
        count = asyncio.run(reindex_all())
    # Rattrapé large : c'est un point d'entrée en ligne de commande, il doit
    # rendre un message lisible et un code de sortie, pas une trace brute.
    except Exception as exc:
        print(f"✗ réindexation interrompue : {exc}", file=sys.stderr)
        return 1
    print(f"✓ {count} fragments réindexés")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
