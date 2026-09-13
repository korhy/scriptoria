"""Suppression d'un document : index, base et fichiers, sans rien laisser derrière.

Postgres est la source de vérité, Elasticsearch un index jetable. D'où l'ordre :
**l'index d'abord, la base ensuite, les fichiers en dernier.** Si la base échoue
après l'index, `make reindex` répare. Dans l'ordre inverse, une panne d'ES
laisserait la recherche citer un document qui n'existe plus.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from elasticsearch import ConnectionError as EsConnectionError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.sql.dml import Delete

from scriptoria.api.deps import get_db, get_es, get_queue
from scriptoria.api.routers import documents as documents_router
from scriptoria.config import get_settings
from scriptoria.db.models import Document, Job
from scriptoria.domain.enums import DocumentStatus, JobStatus


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    """`journal` est partagé avec l'index et les fichiers : il fixe l'ordre des gestes."""

    def __init__(self, document: Document, journal: list[str]) -> None:
        self.document = document
        self.journal = journal
        self.jobs_actifs: list[Job] = []

    async def get(self, model: Any, ident: Any) -> Any:
        return self.document if ident == self.document.id else None

    async def execute(self, statement: Any) -> FakeResult:
        if isinstance(statement, Delete):
            self.journal.append("base")
            return FakeResult([])
        return FakeResult(self.jobs_actifs)

    async def commit(self) -> None:
        self.journal.append("commit")

    async def rollback(self) -> None:
        return None


class FakeEs:
    def __init__(self, journal: list[str]) -> None:
        self.journal = journal
        self.appels: list[dict[str, Any]] = []
        self.en_panne = False

    async def delete_by_query(self, **kwargs: Any) -> dict[str, Any]:
        if self.en_panne:
            raise EsConnectionError("elasticsearch injoignable")
        self.journal.append("index")
        self.appels.append(kwargs)
        return {"deleted": 1}


@pytest.fixture
def document() -> Document:
    now = datetime.now(UTC)
    return Document(
        id=uuid4(),
        source_filename="scan.png",
        status=DocumentStatus.INDEXED,
        page_count=1,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def journal() -> list[str]:
    return []


@pytest.fixture
def fichiers_supprimes(monkeypatch: pytest.MonkeyPatch, journal: list[str]) -> list[tuple]:
    supprimes: list[tuple[Path, UUID]] = []

    def faux_remove(data_dir: Path, document_id: UUID) -> None:
        journal.append("fichiers")
        supprimes.append((data_dir, document_id))

    monkeypatch.setattr(documents_router, "remove_document_files", faux_remove)
    return supprimes


@pytest.fixture
def jobs_arq_actifs(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Identifiants que arq déclare encore en file ou en cours."""
    actifs: set[str] = set()

    async def faux_etat(queue: Any, arq_job_id: str | None) -> bool:
        return arq_job_id in actifs

    monkeypatch.setattr(documents_router, "_arq_job_active", faux_etat)
    return actifs


@pytest.fixture
def contexte(
    app: FastAPI,
    document: Document,
    journal: list[str],
    fichiers_supprimes: list[tuple],
    jobs_arq_actifs: set[str],
) -> Iterator[tuple[TestClient, FakeSession, FakeEs]]:
    session = FakeSession(document, journal)
    es = FakeEs(journal)

    async def _db() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_es] = lambda: es
    app.dependency_overrides[get_queue] = lambda: object()
    yield TestClient(app, raise_server_exceptions=False), session, es


def job_en_cours(kind: str = "transcribe") -> Job:
    return Job(document_id=uuid4(), kind=kind, status=JobStatus.RUNNING, arq_job_id="job-1")


# --- Chemin nominal ---------------------------------------------------------


def test_l_index_puis_la_base_puis_les_fichiers(
    contexte: tuple[TestClient, FakeSession, FakeEs], document: Document, journal: list[str]
) -> None:
    client, _, _ = contexte

    response = client.delete(f"/documents/{document.id}")

    assert response.status_code == 204, response.text
    # Commit avant les fichiers : un commit raté laisserait sinon un document
    # en base privé de ses images.
    assert journal == ["index", "base", "commit", "fichiers"]


def test_seuls_les_fragments_du_document_quittent_l_index(
    contexte: tuple[TestClient, FakeSession, FakeEs], document: Document
) -> None:
    client, _, es = contexte

    client.delete(f"/documents/{document.id}")

    [appel] = es.appels
    assert appel["index"] == get_settings().elasticsearch_index
    assert appel["query"] == {"term": {"document_id": str(document.id)}}
    # Index absent : il n'y a rien à retirer, ce n'est pas une panne.
    assert appel["ignore_unavailable"] is True
    # Une recherche lancée juste après ne doit plus trouver le document.
    assert appel["refresh"] is True


def test_les_fichiers_retires_sont_ceux_du_document(
    contexte: tuple[TestClient, FakeSession, FakeEs],
    document: Document,
    fichiers_supprimes: list[tuple],
) -> None:
    client, _, _ = contexte

    client.delete(f"/documents/{document.id}")

    assert fichiers_supprimes == [(get_settings().data_dir, document.id)]


# --- Refus ------------------------------------------------------------------


def test_un_document_inconnu_donne_404_sans_rien_supprimer(
    contexte: tuple[TestClient, FakeSession, FakeEs], journal: list[str]
) -> None:
    client, _, _ = contexte

    response = client.delete(f"/documents/{uuid4()}")

    assert response.status_code == 404
    assert journal == []


def test_un_job_encore_actif_bloque_la_suppression(
    contexte: tuple[TestClient, FakeSession, FakeEs],
    document: Document,
    journal: list[str],
    jobs_arq_actifs: set[str],
) -> None:
    """Supprimer sous un worker qui écrit ferait échouer la tâche au milieu d'une page."""
    client, session, _ = contexte
    session.jobs_actifs = [job_en_cours("transcribe")]
    jobs_arq_actifs.add("job-1")

    response = client.delete(f"/documents/{document.id}")

    assert response.status_code == 409
    assert "'transcribe'" in response.json()["detail"]
    assert journal == []


def test_un_job_marque_en_cours_mais_mort_dans_arq_ne_bloque_pas(
    contexte: tuple[TestClient, FakeSession, FakeEs], document: Document
) -> None:
    """La base peut dire `running` d'un job qu'arq a abandonné depuis longtemps.

    C'est l'état qu'a laissé le défaut d'annulation corrigé le 2026-09-13. Se fier
    à la base rendrait ces documents indélébiles ; arq, lui, sait ce qui tourne.
    """
    client, session, _ = contexte
    session.jobs_actifs = [job_en_cours("transcribe")]

    response = client.delete(f"/documents/{document.id}")

    assert response.status_code == 204, response.text


def test_elasticsearch_en_panne_ne_supprime_rien(
    contexte: tuple[TestClient, FakeSession, FakeEs], document: Document, journal: list[str]
) -> None:
    """Supprimer la base sans l'index laisserait la recherche citer un document disparu."""
    client, _, es = contexte
    es.en_panne = True

    response = client.delete(f"/documents/{document.id}")

    assert response.status_code == 503
    assert journal == []


async def test_un_job_sans_identifiant_arq_est_presume_actif() -> None:
    """Sans identifiant, impossible de prouver qu'il est mort : on ne supprime pas."""
    assert await documents_router._arq_job_active(object(), None) is True
