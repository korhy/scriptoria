"""Tâche d'indexation du worker.

Deux propriétés comptent ici :

1. **Seule la dernière révision validée d'une page est indexée.** Indexer une
   sortie brute d'OCR non relue reviendrait à répondre aux recherches avec du
   texte que personne n'a vérifié.
2. **Un document incomplet n'est pas indexé à moitié en silence.** S'il reste
   une page sans révision validée, la tâche échoue en la nommant.
"""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from scriptoria.config import Settings
from scriptoria.db.models import Document, Job, Page, Transcription
from scriptoria.domain.enums import DocumentStatus, JobStatus, TranscriptionOrigin
from scriptoria.services.indexing import IndexingError
from scriptoria.workers.tasks import index_document


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
    def __init__(self, document: Document | None, pages: list[Page], job: Job | None) -> None:
        self.document = document
        self.pages = pages
        self.job = job

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False

    async def get(self, model: Any, ident: Any) -> Any:
        return self.document

    async def execute(self, statement: Any) -> FakeResult:
        entity = statement.column_descriptions[0]["entity"]
        if entity is Page:
            return FakeResult(self.pages)
        if entity is Job:
            return FakeResult([self.job] if self.job is not None else [])
        return FakeResult([])

    def add(self, obj: Any) -> None:
        return None

    async def commit(self) -> None:
        return None


class FakeIndices:
    def __init__(self) -> None:
        self.created: list[str] = []

    async def exists(self, index: str) -> bool:
        return True

    async def create(self, index: str, **body: Any) -> dict:
        self.created.append(index)
        return {}


class FakeEs:
    def __init__(self) -> None:
        self.indices = FakeIndices()
        self.indexed: list[dict] = []

    async def bulk(self, *, operations: list[dict], **kwargs: Any) -> dict:
        self.indexed.extend(operations)
        return {"errors": False, "items": []}


def transcription(
    page_id: UUID,
    revision: int,
    *,
    origin: TranscriptionOrigin,
    validee: bool,
    texte: str,
) -> Transcription:
    return Transcription(
        id=uuid4(),
        page_id=page_id,
        revision=revision,
        content_markdown=texte,
        origin=origin,
        model_name="qwen2.5vl:7b" if origin is TranscriptionOrigin.OCR else None,
        is_validated=validee,
        created_at=datetime.now(UTC),
    )


def page_validee(document_id: UUID, numero: int, texte: str = "texte relu") -> Page:
    """Page dont l'OCR brut a été corrigé puis validé par un humain."""
    page_id = uuid4()
    page = Page(
        id=page_id,
        document_id=document_id,
        page_number=numero,
        raw_image_path=f"inbox/{document_id}/{numero:04d}.png",
        preprocessed_image_path=f"images/{document_id}/{numero:04d}.png",
    )
    page.transcriptions = [
        transcription(
            page_id, 1, origin=TranscriptionOrigin.OCR, validee=False, texte="texte brut de l'OCR"
        ),
        transcription(page_id, 2, origin=TranscriptionOrigin.HUMAN, validee=True, texte=texte),
    ]
    return page


@pytest.fixture
def contexte(tmp_path: Any) -> dict[str, Any]:
    document_id = uuid4()
    document = Document(
        id=document_id,
        source_filename="scan.png",
        status=DocumentStatus.VALIDATED,
        page_count=1,
    )
    session = FakeSession(
        document=document,
        pages=[page_validee(document_id, 1)],
        job=Job(document_id=document_id, kind="index", status=JobStatus.QUEUED),
    )
    return {
        "settings": Settings(data_dir=tmp_path),
        "sessionmaker": lambda: session,
        "es": FakeEs(),
        "ollama": object(),
        "session": session,
        "document": document,
    }


@pytest.fixture
def vectorisations(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Remplace l'appel d'embeddings et retient les lots soumis."""
    lots: list[list[str]] = []

    async def faux_embed_texts(client: Any, texts: list[str], model: str) -> list[list[float]]:
        lots.append(list(texts))
        return [[0.0] * 1024 for _ in texts]

    monkeypatch.setattr("scriptoria.workers.tasks.embed_texts", faux_embed_texts)
    return lots


# --- Chemin nominal ---------------------------------------------------------


async def test_seule_la_revision_validee_est_indexee(
    contexte: dict[str, Any], vectorisations: list[list[str]]
) -> None:
    """Indexer du texte non relu reviendrait à répondre avec du non vérifié."""
    await index_document(contexte, str(contexte["document"].id))

    assert vectorisations == [["texte relu"]]


async def test_le_fragment_est_ecrit_sous_un_identifiant_deterministe(
    contexte: dict[str, Any], vectorisations: list[list[str]]
) -> None:
    document_id = contexte["document"].id

    await index_document(contexte, str(document_id))

    entete = contexte["es"].indexed[0]
    assert entete["index"]["_id"] == f"{document_id}:1"


async def test_le_document_finit_indexe(
    contexte: dict[str, Any], vectorisations: list[list[str]]
) -> None:
    await index_document(contexte, str(contexte["document"].id))

    assert contexte["document"].status is DocumentStatus.INDEXED
    assert contexte["session"].job.status is JobStatus.DONE


async def test_la_tache_retourne_le_nombre_de_fragments(
    contexte: dict[str, Any], vectorisations: list[list[str]]
) -> None:
    """`make reindex` s'en sert pour dire ce qu'il a reconstruit."""
    document_id = contexte["document"].id
    contexte["session"].pages = [page_validee(document_id, 1), page_validee(document_id, 2)]

    assert await index_document(contexte, str(document_id)) == 2


async def test_les_fragments_sont_vectorises_par_lots(
    contexte: dict[str, Any], vectorisations: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """200 pages en un seul appel feraient déborder les 16 Go de la machine."""
    monkeypatch.setattr("scriptoria.workers.tasks.EMBEDDING_BATCH_SIZE", 2)
    document_id = contexte["document"].id
    contexte["session"].pages = [page_validee(document_id, numero) for numero in range(1, 6)]

    await index_document(contexte, str(document_id))

    assert [len(lot) for lot in vectorisations] == [2, 2, 1]


# --- Refus ------------------------------------------------------------------


async def test_une_page_non_validee_empeche_l_indexation(
    contexte: dict[str, Any], vectorisations: list[list[str]]
) -> None:
    """Indexer un document à moitié relu le ferait passer pour complet."""
    document_id = contexte["document"].id
    non_relue = page_validee(document_id, 2)
    non_relue.transcriptions = [
        transcription(
            non_relue.id, 1, origin=TranscriptionOrigin.OCR, validee=False, texte="texte brut"
        )
    ]
    contexte["session"].pages = [page_validee(document_id, 1), non_relue]

    with pytest.raises(IndexingError) as erreur:
        await index_document(contexte, str(document_id))

    assert "2" in str(erreur.value)
    assert contexte["document"].status is DocumentStatus.FAILED


async def test_un_echec_de_vectorisation_marque_le_document(
    contexte: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def echec(*args: Any, **kwargs: Any) -> list[list[float]]:
        raise RuntimeError("Ollama injoignable")

    monkeypatch.setattr("scriptoria.workers.tasks.embed_texts", echec)

    with pytest.raises(RuntimeError):
        await index_document(contexte, str(contexte["document"].id))

    assert contexte["document"].status is DocumentStatus.FAILED
    assert contexte["session"].job.status is JobStatus.FAILED


async def test_une_annulation_pendant_l_indexation_marque_le_document_en_echec(
    contexte: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un délai dépassé ou un arrêt du worker annule la tâche sans lever d'Exception."""

    async def vectorisation_interminable(*args: Any, **kwargs: Any) -> list[list[float]]:
        await asyncio.sleep(3600)
        raise AssertionError("inatteignable")

    monkeypatch.setattr("scriptoria.workers.tasks.embed_texts", vectorisation_interminable)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(index_document(contexte, str(contexte["document"].id)), 0.05)

    assert contexte["document"].status is DocumentStatus.FAILED
    assert contexte["session"].job.status is JobStatus.FAILED
    assert "interrompu" in (contexte["session"].job.error or "")


async def test_un_document_absent_n_est_pas_une_erreur(
    contexte: dict[str, Any], vectorisations: list[list[str]]
) -> None:
    contexte["session"].document = None

    assert await index_document(contexte, str(uuid4())) == 0
    assert vectorisations == []
