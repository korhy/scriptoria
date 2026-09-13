"""Suivi d'un document : la progression de l'OCR doit se lire sans deviner.

Un lot de 200 pages reste `transcribing` près de trois heures. Sans compteur,
rien ne distingue un worker qui avance d'un worker bloqué à la première page.
Le commit a lieu page par page : l'information existe en base, il suffit de
l'exposer — et de ne jamais l'inventer.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scriptoria.api.deps import get_db
from scriptoria.db.models import Document
from scriptoria.domain.enums import DocumentStatus
from scriptoria.schemas.document import DocumentRead


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    """Rend les documents pour `select(Document)`, les comptages pour le reste."""

    def __init__(self, documents: list[Document], counts: dict[UUID, int]) -> None:
        self.documents = documents
        self.counts = counts
        self.count_statements: list[Any] = []

    async def get(self, model: Any, ident: Any) -> Any:
        return next((doc for doc in self.documents if doc.id == ident), None)

    async def execute(self, statement: Any) -> FakeResult:
        if statement.column_descriptions[0]["expr"] is Document:
            return FakeResult(self.documents)
        self.count_statements.append(statement)
        return FakeResult(list(self.counts.items()))

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def nouveau_document(page_count: int = 3) -> Document:
    now = datetime.now(UTC)
    return Document(
        id=uuid4(),
        source_filename="scan.png",
        status=DocumentStatus.TRANSCRIBING,
        page_count=page_count,
        created_at=now,
        updated_at=now,
    )


def build_client(app: FastAPI, session: FakeSession) -> TestClient:
    async def _db() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_db] = _db
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def document() -> Document:
    return nouveau_document()


@pytest.fixture
def contexte(app: FastAPI, document: Document) -> Iterator[tuple[TestClient, FakeSession]]:
    session = FakeSession([document], {document.id: 2})
    yield build_client(app, session), session


def test_le_detail_expose_les_pages_deja_transcrites(
    contexte: tuple[TestClient, FakeSession], document: Document
) -> None:
    client, _ = contexte

    response = client.get(f"/documents/{document.id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pages_transcribed"] == 2
    assert body["page_count"] == 3


def test_un_document_sans_page_transcrite_vaut_zero(
    contexte: tuple[TestClient, FakeSession], document: Document
) -> None:
    """Aucune ligne de comptage en base ne veut pas dire « inconnu » : c'est zéro."""
    client, session = contexte
    session.counts = {}

    response = client.get(f"/documents/{document.id}")

    assert response.status_code == 200, response.text
    assert response.json()["pages_transcribed"] == 0


def test_un_document_inconnu_donne_404_sans_rien_compter(
    contexte: tuple[TestClient, FakeSession],
) -> None:
    client, session = contexte

    response = client.get(f"/documents/{uuid4()}")

    assert response.status_code == 404
    assert session.count_statements == []


def test_la_liste_porte_le_compteur_de_chaque_document_en_une_requete(app: FastAPI) -> None:
    """Une requête de comptage par ligne ferait 50 allers-retours pour une page de liste."""
    premier, second = nouveau_document(), nouveau_document()
    session = FakeSession([premier, second], {premier.id: 3})
    client = build_client(app, session)

    response = client.get("/documents")

    assert response.status_code == 200, response.text
    compteurs = {item["id"]: item["pages_transcribed"] for item in response.json()}
    assert compteurs == {str(premier.id): 3, str(second.id): 0}
    assert len(session.count_statements) == 1


def test_une_liste_vide_ne_lance_aucun_comptage(app: FastAPI) -> None:
    session = FakeSession([], {})
    client = build_client(app, session)

    response = client.get("/documents")

    assert response.status_code == 200
    assert response.json() == []
    assert session.count_statements == []


def test_le_compteur_n_a_pas_de_valeur_par_defaut() -> None:
    """Un défaut à 0 afficherait « 0 page » sur un lot de 200 déjà transcrit.

    Sans défaut, une route qui oublie de le calculer échoue en 500 au lieu de
    mentir avec un chiffre plausible.
    """
    assert DocumentRead.model_fields["pages_transcribed"].is_required()
