"""Réindexation complète depuis Postgres.

Ce script est la garantie opérationnelle qu'Elasticsearch reste jetable : tant
qu'il fonctionne, perdre le volume ES n'est pas un incident, on rejoue.

Il **supprime l'index avant de le reconstruire**. C'est délibéré : reconstruire
par-dessus l'existant laisserait survivre des fragments de pages supprimées
depuis, et on ne pourrait plus affirmer que l'index est le reflet de la base.
"""

from typing import Any
from uuid import uuid4

import pytest

from scriptoria.scripts import reindex


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    def __init__(self, ids: list[Any]) -> None:
        self.ids = ids
        self.statements: list[Any] = []

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False

    async def execute(self, statement: Any) -> FakeResult:
        self.statements.append(statement)
        return FakeResult(self.ids)


class FakeIndices:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.created: list[str] = []

    async def delete(self, index: str, **kwargs: Any) -> dict:
        self.deleted.append(index)
        return {}

    async def exists(self, index: str) -> bool:
        # Après la suppression, l'index n'existe plus : il doit être recréé.
        return False

    async def create(self, index: str, **body: Any) -> dict:
        self.created.append(index)
        return {}


class FakeEs:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.indices = FakeIndices()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Remplace la stack réelle : ni Postgres, ni Elasticsearch, ni Ollama."""
    session = FakeSession([uuid4(), uuid4()])
    engine = FakeEngine()
    es = FakeEs()
    indexes: list[str] = []

    async def faux_index_document(ctx: dict[str, Any], document_id: str) -> int:
        indexes.append(document_id)
        return 3

    monkeypatch.setattr(reindex, "create_engine", lambda settings: engine)
    monkeypatch.setattr(reindex, "create_sessionmaker", lambda engine: lambda: session)
    monkeypatch.setattr(reindex, "AsyncElasticsearch", lambda *a, **k: es)
    monkeypatch.setattr(reindex, "index_document", faux_index_document)
    return {"session": session, "engine": engine, "es": es, "indexes": indexes}


async def test_l_index_est_supprime_puis_recree(stack: dict[str, Any]) -> None:
    """« Depuis zéro » : aucun fragment d'un état antérieur ne doit survivre."""
    await reindex.reindex_all()

    assert stack["es"].indices.deleted, "l'index n'a pas été supprimé"
    assert stack["es"].indices.created, "l'index n'a pas été recréé"


async def test_chaque_document_valide_est_reindexe(stack: dict[str, Any]) -> None:
    await reindex.reindex_all()

    assert len(stack["indexes"]) == 2


async def test_le_total_des_fragments_est_retourne(stack: dict[str, Any]) -> None:
    """C'est ce nombre que `make reindex` affiche : il doit dire le vrai."""
    assert await reindex.reindex_all() == 6


async def test_la_selection_ne_porte_que_sur_les_documents_valides(
    stack: dict[str, Any],
) -> None:
    """Réindexer un document non relu remettrait du texte non vérifié dans l'index."""
    await reindex.reindex_all()

    requete = str(stack["session"].statements[0]).lower()
    assert "status" in requete and "in" in requete


async def test_les_connexions_sont_refermees(stack: dict[str, Any]) -> None:
    await reindex.reindex_all()

    assert stack["engine"].disposed
    assert stack["es"].closed


async def test_les_connexions_sont_refermees_meme_en_echec(
    stack: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un script qui laisse des connexions ouvertes derrière lui fait traîner la VM."""

    async def echec(ctx: dict[str, Any], document_id: str) -> int:
        raise RuntimeError("Ollama injoignable")

    monkeypatch.setattr(reindex, "index_document", echec)

    with pytest.raises(RuntimeError):
        await reindex.reindex_all()

    assert stack["engine"].disposed
    assert stack["es"].closed
