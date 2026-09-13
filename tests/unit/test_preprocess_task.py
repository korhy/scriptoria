"""Tâche de prétraitement du worker : ce qu'elle laisse derrière elle quand elle échoue.

Le chemin nominal (OpenCV sur de vraies images) est couvert par les tests
d'intégration. Ici, une seule propriété : **un document ne reste jamais dans un
état « en cours » après la fin de la tâche**, qu'elle ait levé une erreur ou
qu'elle ait été annulée par arq.
"""

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from scriptoria.config import Settings
from scriptoria.db.models import Document, Job, Page
from scriptoria.domain.enums import DocumentStatus, JobStatus
from scriptoria.workers.tasks import preprocess_document


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class FakeSession:
    """La lecture des pages est la seule attente simulée : `lecture_pages` la pilote."""

    def __init__(self, document: Document, job: Job) -> None:
        self.document = document
        self.job = job
        self.lecture_pages: Any = None

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False

    async def get(self, model: Any, ident: Any) -> Any:
        return self.document

    async def execute(self, statement: Any) -> FakeResult:
        entity = statement.column_descriptions[0]["entity"]
        if entity is Page and self.lecture_pages is not None:
            await self.lecture_pages()
        if entity is Job:
            return FakeResult([self.job])
        return FakeResult([])

    async def commit(self) -> None:
        return None


@pytest.fixture
def contexte(tmp_path: Path) -> dict[str, Any]:
    document_id = uuid4()
    document = Document(
        id=document_id, source_filename="scan.png", status=DocumentStatus.NEW, page_count=1
    )
    session = FakeSession(
        document, Job(document_id=document_id, kind="preprocess", status=JobStatus.QUEUED)
    )
    return {
        "settings": Settings(data_dir=tmp_path),
        "sessionmaker": lambda: session,
        "session": session,
        "document": document,
    }


async def test_un_echec_marque_le_document_et_releve(contexte: dict[str, Any]) -> None:
    async def panne() -> None:
        raise ConnectionError("postgres perdu")

    contexte["session"].lecture_pages = panne

    with pytest.raises(ConnectionError):
        await preprocess_document(contexte, str(contexte["document"].id))

    assert contexte["document"].status is DocumentStatus.FAILED
    assert contexte["session"].job.status is JobStatus.FAILED
    assert "postgres perdu" in (contexte["session"].job.error or "")


async def test_une_annulation_marque_le_document_en_echec(contexte: dict[str, Any]) -> None:
    """Sans cela, le document reste `preprocessing` et l'OCR ne peut jamais être lancé."""

    async def attente_interminable() -> None:
        await asyncio.sleep(3600)

    contexte["session"].lecture_pages = attente_interminable

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(preprocess_document(contexte, str(contexte["document"].id)), 0.05)

    assert contexte["document"].status is DocumentStatus.FAILED
    assert contexte["session"].job.status is JobStatus.FAILED
    assert "interrompu" in (contexte["session"].job.error or "")
