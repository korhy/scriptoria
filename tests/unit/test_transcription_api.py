"""Déclenchement de l'OCR par l'API.

L'API **enfile**, le worker exécute : une page coûte ~57 s, un document en compte
parfois deux cents. Ces tests vérifient qu'aucun chemin ne fait travailler la
requête HTTP, et que les états incompatibles sont refusés avec un message qui
dit lequel.
"""

from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scriptoria.api.deps import get_db, get_queue
from scriptoria.db.models import Document, Job
from scriptoria.domain.enums import DocumentStatus


class FakeJob:
    job_id = "job-de-test"


class FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple[Any, ...]]] = []

    async def enqueue_job(self, name: str, *args: Any, **kwargs: Any) -> FakeJob:
        self.enqueued.append((name, args))
        return FakeJob()


class FakeSession:
    def __init__(self, document: Document | None) -> None:
        self.document = document
        self.added: list[Any] = []

    async def get(self, model: Any, ident: Any) -> Any:
        return self.document

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.fixture
def document() -> Document:
    return Document(
        id=uuid4(),
        source_filename="scan.png",
        status=DocumentStatus.PREPROCESSED,
        page_count=2,
    )


@pytest.fixture
def contexte(
    app: FastAPI, document: Document
) -> Iterator[tuple[TestClient, FakeQueue, FakeSession]]:
    session = FakeSession(document)
    queue = FakeQueue()

    async def _db() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_queue] = lambda: queue
    yield TestClient(app, raise_server_exceptions=False), queue, session


def test_l_ocr_est_enfile_et_la_requete_rend_la_main(
    contexte: tuple[TestClient, FakeQueue, FakeSession], document: Document
) -> None:
    client, queue, _ = contexte

    response = client.post(f"/documents/{document.id}/transcribe")

    assert response.status_code == 202
    assert queue.enqueued == [("transcribe_document", (str(document.id),))]
    assert response.json()["arq_job_id"] == "job-de-test"


def test_le_job_est_trace_en_base(
    contexte: tuple[TestClient, FakeQueue, FakeSession], document: Document
) -> None:
    """Sans trace, un lot de trois heures est un trou noir pour l'UI."""
    client, _, session = contexte

    client.post(f"/documents/{document.id}/transcribe")

    jobs = [obj for obj in session.added if isinstance(obj, Job)]
    assert [job.kind for job in jobs] == ["transcribe"]


@pytest.mark.parametrize(
    "statut",
    [DocumentStatus.NEW, DocumentStatus.PREPROCESSING, DocumentStatus.TRANSCRIBING],
)
def test_un_document_non_pretraite_est_refuse(
    contexte: tuple[TestClient, FakeQueue, FakeSession],
    document: Document,
    statut: DocumentStatus,
) -> None:
    """OCRiser une image non nettoyée coûterait des heures pour un résultat dégradé."""
    client, queue, _ = contexte
    document.status = statut

    response = client.post(f"/documents/{document.id}/transcribe")

    assert response.status_code == 409
    # Le message doit nommer l'état courant : « refusé » sans raison fait perdre du temps.
    assert statut.value in response.json()["detail"]
    assert queue.enqueued == []


def test_un_document_inconnu_donne_404(
    contexte: tuple[TestClient, FakeQueue, FakeSession],
) -> None:
    client, queue, session = contexte
    session.document = None

    response = client.post(f"/documents/{uuid4()}/transcribe")

    assert response.status_code == 404
    assert queue.enqueued == []
