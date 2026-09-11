"""Fixtures partagées.

Les tests unitaires ne touchent aucun service externe : les dépendances FastAPI
sont remplacées par `app.dependency_overrides`. C'est possible parce que
`api/deps.py` est la seule porte d'entrée vers Postgres, ES et Ollama.
"""

from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scriptoria.api.deps import get_db, get_es, get_ollama
from scriptoria.main import create_app


class FakeResult:
    def scalars(self) -> "FakeResult":
        return self

    def all(self) -> list[Any]:
        return []


class FakeSession:
    """Session minimale : répond aux appels des tests sans base réelle."""

    def __init__(self, *, healthy: bool = True) -> None:
        self.healthy = healthy

    async def execute(self, *args: Any, **kwargs: Any) -> FakeResult:
        if not self.healthy:
            raise ConnectionError("postgres indisponible")
        return FakeResult()

    async def get(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class FakeCluster:
    def __init__(self, *, healthy: bool) -> None:
        self.healthy = healthy

    async def health(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        if not self.healthy:
            raise ConnectionError("elasticsearch indisponible")
        return {"status": "green"}


class FakeEs:
    def __init__(self, *, healthy: bool = True) -> None:
        self.cluster = FakeCluster(healthy=healthy)


class FakeResponse:
    def raise_for_status(self) -> None:
        return None


class FakeOllama:
    def __init__(self, *, healthy: bool = True) -> None:
        self.healthy = healthy

    async def get(self, *args: Any, **kwargs: Any) -> FakeResponse:
        if not self.healthy:
            raise ConnectionError("ollama indisponible")
        return FakeResponse()


@pytest.fixture
def app() -> Iterator[FastAPI]:
    """Application de test — le lifespan n'ouvre aucune connexion réelle ici."""
    application = create_app()
    yield application
    application.dependency_overrides.clear()


def _build_client(app: FastAPI, **health: bool) -> TestClient:
    """Client dont les trois dépendances sont contrôlées individuellement.

    Volontairement sans gestionnaire de contexte : entrer dans le contexte
    déclencherait le `lifespan`, qui ouvre de vrais clients et crée `/data`.
    Comme les trois dépendances sont surchargées ici, `app.state` n'est jamais lu.
    """

    async def _db() -> AsyncIterator[FakeSession]:
        yield FakeSession(healthy=health.get("db", True))

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_es] = lambda: FakeEs(healthy=health.get("es", True))
    app.dependency_overrides[get_ollama] = lambda: FakeOllama(healthy=health.get("ollama", True))
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_factory(app: FastAPI) -> Callable[..., TestClient]:
    """Fabrique un client en choisissant quelles dépendances sont en panne.

    `client_factory(db=False)` simule Postgres indisponible, les autres saines.
    """

    def _factory(**health: bool) -> TestClient:
        return _build_client(app, **health)

    return _factory


@pytest.fixture
def client(client_factory: Callable[..., TestClient]) -> TestClient:
    """Client dont les trois dépendances répondent normalement."""
    return client_factory()
