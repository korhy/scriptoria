"""Dépendances FastAPI.

Les clients coûteux (moteur SQLAlchemy, Elasticsearch, HTTP vers Ollama) sont
créés une seule fois dans le `lifespan` de l'application et exposés ici. Les
routers ne construisent jamais leurs propres connexions : c'est ce qui rend les
tests capables de les remplacer par `app.dependency_overrides`.
"""

from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from elasticsearch import AsyncElasticsearch
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from scriptoria.config import Settings, get_settings
from scriptoria.db.session import session_scope


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async for session in session_scope(request.app.state.sessionmaker):
        yield session


def get_es(request: Request) -> AsyncElasticsearch:
    return request.app.state.es


def get_ollama(request: Request) -> httpx.AsyncClient:
    return request.app.state.ollama


DbSession = Annotated[AsyncSession, Depends(get_db)]
EsClient = Annotated[AsyncElasticsearch, Depends(get_es)]
OllamaClient = Annotated[httpx.AsyncClient, Depends(get_ollama)]
AppSettings = Annotated[Settings, Depends(get_settings)]
