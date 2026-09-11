"""Construction de l'application FastAPI.

Le `lifespan` crée les clients partagés une seule fois et les ferme proprement à
l'arrêt. Aucun module ne crée de connexion à l'import : c'est ce qui permet
d'importer l'application dans les tests sans stack démarrée.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from arq import create_pool
from arq.connections import RedisSettings
from elasticsearch import AsyncElasticsearch
from fastapi import FastAPI

from scriptoria.api.routers import documents, health, pages, search
from scriptoria.config import Settings, get_settings
from scriptoria.db.session import create_engine, create_sessionmaker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()

    engine = create_engine(settings)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)
    app.state.es = AsyncElasticsearch(settings.elasticsearch_url)
    app.state.ollama = httpx.AsyncClient(
        base_url=settings.ollama_base_url,
        timeout=settings.ollama_timeout_seconds,
    )
    app.state.queue = await create_pool(RedisSettings.from_dsn(settings.redis_url))

    for directory in (settings.inbox_dir, settings.images_dir, settings.markdown_dir):
        directory.mkdir(parents=True, exist_ok=True)

    logger.info(
        "scriptoria prêt — ollama=%s es=%s",
        settings.ollama_base_url,
        settings.elasticsearch_url,
    )
    try:
        yield
    finally:
        await app.state.queue.aclose()
        await app.state.ollama.aclose()
        await app.state.es.close()
        await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    app = FastAPI(
        title="Scriptoria",
        description=(
            "Numérisation de documents papier et RAG documentaire, 100% local. "
            "Aucune inférence ne quitte la machine."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(pages.router)
    app.include_router(search.router)
    return app


app = create_app()
