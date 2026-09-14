"""Pages et corrections humaines — surface consommée par l'UI de validation.

L'invariant du projet se joue ici : **une correction crée une révision `n+1`,
elle n'écrase jamais la précédente.** C'est ce qui préserve l'historique
image → texte brut → texte corrigé, et ce qui permettra un jour de mesurer la
qualité de l'OCR sur des cas réels. Un `UPDATE` à la place d'un `INSERT` détruit
cette possibilité sans que rien ne le signale.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scriptoria.api.deps import get_db, get_queue
from scriptoria.db.models import ConfidenceBlock, Document, Job, Page, Transcription
from scriptoria.domain.enums import DocumentStatus, TranscriptionOrigin


class FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class FakeJob:
    job_id = "job-de-test"


class FakeQueue:
    """File arq simulée : enregistre ce qui a été enfilé, n'exécute rien."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple[Any, ...]]] = []

    async def enqueue_job(self, name: str, *args: Any, **kwargs: Any) -> FakeJob:
        self.enqueued.append((name, args))
        return FakeJob()


class FakeSession:
    def __init__(self, document: Document | None, pages: list[Page]) -> None:
        self.document = document
        self.pages = pages
        self.added: list[Any] = []

    async def get(self, model: Any, ident: Any) -> Any:
        return self.document if model is Document else None

    async def execute(self, statement: Any) -> FakeResult:
        entity = statement.column_descriptions[0]["entity"]
        return FakeResult(self.pages if entity is Page else [])

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def revision_ocr(page_id: UUID, revision: int = 1, *, validee: bool = False) -> Transcription:
    return Transcription(
        id=uuid4(),
        page_id=page_id,
        revision=revision,
        content_markdown="| Encre | 6 | 28,60 | 173,40 |",
        origin=TranscriptionOrigin.OCR,
        model_name="qwen2.5vl:7b",
        is_validated=validee,
        # Le défaut de la colonne ne s'applique qu'à l'écriture en base : un objet
        # construit en mémoire doit le porter lui-même.
        bulk_validated=False,
        created_at=datetime.now(UTC),
        confidence_blocks=[
            ConfidenceBlock(start_offset=0, end_offset=10, score=0.15, method="arithmetic")
        ],
    )


def page_avec(transcriptions: list[Transcription], document_id: UUID) -> Page:
    page_id = transcriptions[0].page_id if transcriptions else uuid4()
    page = Page(
        id=page_id,
        document_id=document_id,
        page_number=1,
        raw_image_path=f"inbox/{document_id}/0001.png",
        preprocessed_image_path=f"images/{document_id}/0001.png",
    )
    page.transcriptions = transcriptions
    return page


def build_client(app: FastAPI, session: FakeSession, queue: FakeQueue) -> TestClient:
    """Client dont la base et la file sont simulées."""

    async def _db() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_queue] = lambda: queue
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def document() -> Document:
    return Document(
        id=uuid4(),
        source_filename="scan.png",
        status=DocumentStatus.AWAITING_VALIDATION,
        page_count=1,
    )


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def contexte(
    app: FastAPI, document: Document, queue: FakeQueue
) -> Iterator[tuple[TestClient, FakeSession, Page]]:
    page = page_avec([revision_ocr(uuid4())], document.id)
    session = FakeSession(document, [page])
    yield build_client(app, session, queue), session, page


# --- Lecture ----------------------------------------------------------------


def test_une_page_expose_ses_revisions_et_ses_blocs(
    contexte: tuple[TestClient, FakeSession, Page],
) -> None:
    """Sans les blocs, l'UI de validation n'a rien à surligner."""
    client, _, page = contexte

    response = client.get(f"/pages/{page.id}")

    assert response.status_code == 200
    corps = response.json()
    assert corps["page_number"] == 1
    assert len(corps["transcriptions"]) == 1
    assert corps["transcriptions"][0]["confidence_blocks"][0]["method"] == "arithmetic"


def test_le_score_de_confiance_de_la_page_est_expose(
    contexte: tuple[TestClient, FakeSession, Page],
) -> None:
    """L'UI doit pouvoir trier les pages par urgence de relecture."""
    client, _, page = contexte

    corps = client.get(f"/pages/{page.id}").json()

    assert corps["confidence_score"] == pytest.approx(0.15)


def test_une_page_inconnue_donne_404(app: FastAPI) -> None:
    client = build_client(app, FakeSession(None, []), FakeQueue())

    assert client.get(f"/pages/{uuid4()}").status_code == 404


# --- Correction : l'invariant -----------------------------------------------


def test_une_correction_cree_une_revision_suivante(
    contexte: tuple[TestClient, FakeSession, Page],
) -> None:
    client, _, page = contexte

    response = client.post(
        f"/pages/{page.id}/corrections",
        json={"content_markdown": "| Encre | 6 | 28,90 | 173,40 |", "validate_now": True},
    )

    assert response.status_code == 201
    corps = response.json()
    assert corps["revision"] == 2
    assert corps["origin"] == "human"


def test_la_revision_corrigee_reste_intacte(
    contexte: tuple[TestClient, FakeSession, Page],
) -> None:
    """Le cœur de l'invariant : le texte d'origine de l'OCR doit survivre."""
    client, _, page = contexte
    original = page.transcriptions[0].content_markdown

    client.post(
        f"/pages/{page.id}/corrections",
        json={"content_markdown": "texte entièrement réécrit", "validate_now": True},
    )

    assert page.transcriptions[0].content_markdown == original
    assert page.transcriptions[0].origin is TranscriptionOrigin.OCR


def test_une_correction_humaine_ne_porte_aucun_modele(
    contexte: tuple[TestClient, FakeSession, Page],
) -> None:
    client, _, page = contexte

    client.post(
        f"/pages/{page.id}/corrections",
        json={"content_markdown": "texte relu", "validate_now": True},
    )

    nouvelle = page.transcriptions[-1]
    assert nouvelle.origin is TranscriptionOrigin.HUMAN
    assert nouvelle.model_name is None


def test_la_numerotation_suit_la_derniere_revision(
    contexte: tuple[TestClient, FakeSession, Page],
) -> None:
    client, _, page = contexte
    page.transcriptions.append(revision_ocr(page.id, revision=2))

    corps = client.post(
        f"/pages/{page.id}/corrections",
        json={"content_markdown": "troisième lecture", "validate_now": False},
    ).json()

    assert corps["revision"] == 3


def test_une_page_jamais_transcrite_accepte_une_saisie_manuelle(
    app: FastAPI, document: Document
) -> None:
    """Une page que l'OCR a échoué à lire doit pouvoir être saisie à la main."""
    page = page_avec([], document.id)
    client = build_client(app, FakeSession(document, [page]), FakeQueue())

    corps = client.post(
        f"/pages/{page.id}/corrections",
        json={"content_markdown": "saisie manuelle", "validate_now": True},
    ).json()

    assert corps["revision"] == 1


def test_une_correction_vide_est_refusee(
    contexte: tuple[TestClient, FakeSession, Page],
) -> None:
    """Valider une page avec un texte vide effacerait la transcription."""
    client, _, page = contexte

    response = client.post(
        f"/pages/{page.id}/corrections",
        json={"content_markdown": "   ", "validate_now": True},
    )

    assert response.status_code == 422


# --- Progression du document ------------------------------------------------


def test_le_document_passe_en_valide_quand_toutes_ses_pages_le_sont(
    contexte: tuple[TestClient, FakeSession, Page], document: Document
) -> None:
    client, _, page = contexte

    corps = client.post(
        f"/pages/{page.id}/corrections",
        json={"content_markdown": "page relue", "validate_now": True},
    ).json()

    assert document.status is DocumentStatus.VALIDATED
    assert corps["document_status"] == "validated"


def test_une_page_non_validee_retient_le_document(app: FastAPI, document: Document) -> None:
    """Un document n'est validé que page à page : aucune approbation globale."""
    premiere = page_avec([revision_ocr(uuid4())], document.id)
    seconde = page_avec([revision_ocr(uuid4())], document.id)
    document.page_count = 2
    client = build_client(app, FakeSession(document, [premiere, seconde]), FakeQueue())

    client.post(
        f"/pages/{premiere.id}/corrections",
        json={"content_markdown": "première page relue", "validate_now": True},
    )

    assert document.status is DocumentStatus.AWAITING_VALIDATION


def test_une_correction_sans_validation_ne_fait_pas_avancer_le_document(
    contexte: tuple[TestClient, FakeSession, Page], document: Document
) -> None:
    """Enregistrer un brouillon de correction n'est pas valider."""
    client, _, page = contexte

    client.post(
        f"/pages/{page.id}/corrections",
        json={"content_markdown": "correction en cours", "validate_now": False},
    )

    assert document.status is DocumentStatus.AWAITING_VALIDATION


def test_une_page_sans_transcription_n_a_pas_de_score(app: FastAPI, document: Document) -> None:
    """Pas de transcription, pas de score — surtout pas un 1,00 rassurant."""
    page = page_avec([], document.id)
    client = build_client(app, FakeSession(document, [page]), FakeQueue())

    corps = client.get(f"/pages/{page.id}").json()

    assert corps["confidence_score"] is None
    assert corps["transcriptions"] == []


def test_une_correction_sur_une_page_orpheline_donne_404(app: FastAPI) -> None:
    """Une page sans document n'existe pas ; le dire plutôt que de lever un 500."""
    page = page_avec([revision_ocr(uuid4())], uuid4())
    client = build_client(app, FakeSession(None, [page]), FakeQueue())

    response = client.post(
        f"/pages/{page.id}/corrections",
        json={"content_markdown": "texte", "validate_now": True},
    )

    assert response.status_code == 404
    assert "document" in response.json()["detail"]


def test_une_correction_sur_une_page_inconnue_donne_404(app: FastAPI, document: Document) -> None:
    session = FakeSession(document, [])
    client = build_client(app, session, FakeQueue())

    response = client.post(
        f"/pages/{uuid4()}/corrections",
        json={"content_markdown": "texte", "validate_now": True},
    )

    assert response.status_code == 404
    assert session.added == [], "rien ne doit être écrit pour une page inexistante"


# --- Enchaînement vers l'indexation -----------------------------------------


def test_l_indexation_est_enfilee_quand_le_document_devient_valide(
    contexte: tuple[TestClient, FakeSession, Page],
    queue: FakeQueue,
    document: Document,
) -> None:
    """Vectoriser 200 pages prend des minutes : la requête HTTP enfile, elle n'exécute pas."""
    client, _, page = contexte

    client.post(
        f"/pages/{page.id}/corrections",
        json={"content_markdown": "page relue", "validate_now": True},
    )

    assert queue.enqueued == [("index_document", (str(document.id),))]


def test_le_job_d_indexation_est_trace_en_base(
    contexte: tuple[TestClient, FakeSession, Page],
) -> None:
    client, session, page = contexte

    client.post(
        f"/pages/{page.id}/corrections",
        json={"content_markdown": "page relue", "validate_now": True},
    )

    jobs = [obj for obj in session.added if isinstance(obj, Job)]
    assert [job.kind for job in jobs] == ["index"]


def test_rien_n_est_enfile_tant_qu_une_page_reste_a_valider(
    app: FastAPI, document: Document
) -> None:
    premiere = page_avec([revision_ocr(uuid4())], document.id)
    seconde = page_avec([revision_ocr(uuid4())], document.id)
    document.page_count = 2
    queue = FakeQueue()
    client = build_client(app, FakeSession(document, [premiere, seconde]), queue)

    client.post(
        f"/pages/{premiere.id}/corrections",
        json={"content_markdown": "première page relue", "validate_now": True},
    )

    assert queue.enqueued == []


def test_une_correction_sans_validation_n_enfile_rien(
    contexte: tuple[TestClient, FakeSession, Page], queue: FakeQueue
) -> None:
    client, _, page = contexte

    client.post(
        f"/pages/{page.id}/corrections",
        json={"content_markdown": "brouillon", "validate_now": False},
    )

    assert queue.enqueued == []
