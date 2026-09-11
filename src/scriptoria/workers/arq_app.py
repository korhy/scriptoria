"""Configuration du worker arq.

Lancé par le service `worker` :
    arq scriptoria.workers.arq_app.WorkerSettings
"""

import logging
from collections.abc import Callable
from typing import Any, ClassVar

import httpx
from arq.connections import RedisSettings
from elasticsearch import AsyncElasticsearch

from scriptoria.config import get_settings
from scriptoria.db.session import create_engine, create_sessionmaker
from scriptoria.workers.tasks import index_document, preprocess_document, transcribe_document

logger = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    """Ouvre les clients partagés une fois pour toute la vie du worker."""
    settings = get_settings()
    # Sans cela, les logs applicatifs du worker sont silencieusement perdus :
    # arq configure son propre logger, pas la racine. Or c'est ici qu'on
    # journalise l'angle de redressement de chaque page — la seule trace
    # permettant de diagnostiquer une sortie OCR médiocre après coup.
    logging.basicConfig(level=settings.log_level, force=True)

    engine = create_engine(settings)

    ctx["settings"] = settings
    ctx["engine"] = engine
    ctx["sessionmaker"] = create_sessionmaker(engine)
    ctx["es"] = AsyncElasticsearch(settings.elasticsearch_url)
    ctx["ollama"] = httpx.AsyncClient(
        base_url=settings.ollama_base_url,
        timeout=settings.ollama_timeout_seconds,
    )
    logger.info("worker prêt — ollama=%s", settings.ollama_base_url)


async def shutdown(ctx: dict[str, Any]) -> None:
    """Ferme ce qui a effectivement été ouvert.

    `shutdown` est appelé même quand `startup` a échoué à mi-parcours. Accéder
    aux clés sans précaution y lèverait un KeyError qui masquerait l'erreur
    d'origine — exactement le genre de panne qu'on passe une heure à diagnostiquer.
    """
    if (ollama := ctx.get("ollama")) is not None:
        await ollama.aclose()
    if (es := ctx.get("es")) is not None:
        await es.close()
    if (engine := ctx.get("engine")) is not None:
        await engine.dispose()


class WorkerSettings:
    functions: ClassVar[list[Callable[..., Any]]] = [
        preprocess_document,
        transcribe_document,
        index_document,
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    # Une seule tâche à la fois : sur 16 Go unifiés, deux OCR vision en parallèle
    # feraient déborder la mémoire et s'échanger le modèle en boucle.
    max_jobs = 1
    job_timeout = 3600
