"""Mise en forme d'un document déjà transcrit, en ligne de commande.

Le script choisit le document et valide la transaction ; la mise en forme elle-même
est testée dans `test_normalization_service.py`. Ce qui compte ici : ne jamais
passer derrière un OCR en cours, et rendre une erreur lisible.
"""

from typing import Any
from uuid import UUID, uuid4

import pytest

from scriptoria.db.models import Document
from scriptoria.domain.enums import DocumentStatus
from scriptoria.scripts import normalize as script


class FakeSession:
    def __init__(self, document: Document | None) -> None:
        self.document = document
        self.commits = 0

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False

    async def get(self, model: Any, ident: Any) -> Document | None:
        return self.document

    async def commit(self) -> None:
        self.commits += 1


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Remplace la base et la mise en forme ; retient les documents mis en forme."""
    document = Document(
        id=uuid4(),
        source_filename="page-01.jpg",
        status=DocumentStatus.AWAITING_VALIDATION,
        page_count=44,
    )
    session = FakeSession(document)
    engine = FakeEngine()
    appels: list[UUID] = []

    async def fausse_mise_en_forme(session: Any, document_id: UUID) -> int:
        appels.append(document_id)
        return 34

    monkeypatch.setattr(script, "create_engine", lambda settings: engine)
    monkeypatch.setattr(script, "create_sessionmaker", lambda engine: lambda: session)
    monkeypatch.setattr(script, "normalize_document", fausse_mise_en_forme)
    return {"document": document, "session": session, "engine": engine, "appels": appels}


async def test_le_document_est_mis_en_forme_et_la_transaction_validee(
    stack: dict[str, Any],
) -> None:
    assert await script.normalize(stack["document"].id) == 34

    assert stack["appels"] == [stack["document"].id]
    assert stack["session"].commits == 1


async def test_un_document_encore_en_cours_d_ocr_est_refuse(stack: dict[str, Any]) -> None:
    """Le worker le mettra en forme après sa dernière page : le faire ici le doublerait."""
    stack["document"].status = DocumentStatus.TRANSCRIBING

    with pytest.raises(script.NormalizationRefusedError, match="attendre la fin de l'OCR"):
        await script.normalize(stack["document"].id)

    assert stack["appels"] == []
    assert stack["session"].commits == 0


async def test_un_document_introuvable_est_refuse(stack: dict[str, Any]) -> None:
    stack["session"].document = None

    with pytest.raises(script.NormalizationRefusedError, match="introuvable"):
        await script.normalize(uuid4())


async def test_les_connexions_sont_refermees_meme_en_echec(stack: dict[str, Any]) -> None:
    stack["session"].document = None

    with pytest.raises(script.NormalizationRefusedError):
        await script.normalize(uuid4())

    assert stack["engine"].disposed


def test_la_ligne_de_commande_annonce_le_nombre_de_pages(
    stack: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert script.main([str(stack["document"].id)]) == 0
    assert "34 page(s) mise(s) en forme" in capsys.readouterr().out


def test_la_ligne_de_commande_rend_une_erreur_lisible(
    stack: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """Un point d'entrée en ligne de commande rend un message et un code, pas une trace."""
    stack["session"].document = None

    assert script.main([str(uuid4())]) == 1
    assert "introuvable" in capsys.readouterr().err
