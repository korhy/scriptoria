"""Galerie des pages et validation groupée — surface consommée par l'écran de validation.

La galerie affiche l'état et le score de **chaque** page d'un coup : sans cela,
l'UI ferait une requête par page, 44 pour un règlement de copropriété, 200 pour
un lot.

La validation groupée crée une révision par page à valider, en une transaction,
et n'enfile qu'**une** indexation. Elle refuse tout dès que ce que le relecteur
avait à l'écran ne correspond plus à la base.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from scriptoria.api.deps import get_db, get_queue
from scriptoria.db.models import ConfidenceBlock, Document, Job, Page, Transcription
from scriptoria.domain.enums import DocumentStatus, TranscriptionOrigin
from scriptoria.schemas.page import PageSummary


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
        self.flushed = False

    async def get(self, model: Any, ident: Any) -> Any:
        return self.document if model is Document else None

    async def execute(self, statement: Any) -> FakeResult:
        entity = statement.column_descriptions[0]["entity"]
        return FakeResult(self.pages if entity is Page else [])

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed = True

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def revision(
    page_id: UUID,
    numero: int = 1,
    *,
    origine: TranscriptionOrigin = TranscriptionOrigin.OCR,
    validee: bool = False,
    scores: tuple[float, ...] = (),
) -> Transcription:
    return Transcription(
        id=uuid4(),
        page_id=page_id,
        revision=numero,
        content_markdown="| Encre | 6 | 28,60 | 173,40 |",
        origin=origine,
        model_name=None,
        is_validated=validee,
        bulk_validated=False,
        created_at=datetime.now(UTC),
        confidence_blocks=[
            ConfidenceBlock(start_offset=0, end_offset=10, score=score, method="arithmetic")
            for score in scores
        ],
    )


def nouvelle_page(document: Document, numero: int, *revisions: Transcription) -> Page:
    page = Page(
        id=revisions[0].page_id if revisions else uuid4(),
        document_id=document.id,
        page_number=numero,
        raw_image_path=f"inbox/{document.id}/{numero:04d}.png",
        preprocessed_image_path=f"images/{document.id}/{numero:04d}.png",
    )
    page.transcriptions = list(revisions)
    return page


def page_ocr(document: Document, numero: int, *, scores: tuple[float, ...] = ()) -> Page:
    return nouvelle_page(document, numero, revision(uuid4(), scores=scores))


def page_validee(document: Document, numero: int) -> Page:
    page_id = uuid4()
    return nouvelle_page(
        document,
        numero,
        revision(page_id),
        revision(page_id, 2, origine=TranscriptionOrigin.HUMAN, validee=True),
    )


def affiche(*pages: Page) -> dict[str, int]:
    """Corps attendu par la route : la dernière révision affichée de chaque page."""
    return {str(page.id): page.transcriptions[-1].revision for page in pages}


def build_client(app: FastAPI, session: FakeSession, queue: FakeQueue) -> TestClient:
    async def _db() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_queue] = lambda: queue
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def document() -> Document:
    return Document(
        id=uuid4(),
        source_filename="reglement.pdf",
        status=DocumentStatus.AWAITING_VALIDATION,
        page_count=2,
    )


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


# --- Galerie : état et score de chaque page ----------------------------------


def test_la_liste_des_pages_porte_l_etat_et_le_score_de_chaque_page(
    app: FastAPI, document: Document, queue: FakeQueue
) -> None:
    douteuse = page_ocr(document, 1, scores=(0.15,))
    relue = page_validee(document, 2)
    vide = nouvelle_page(document, 3)
    client = build_client(app, FakeSession(document, [douteuse, relue, vide]), queue)

    response = client.get(f"/documents/{document.id}/pages")

    assert response.status_code == 200, response.text
    resume = [
        (p["page_number"], p["state"], p["confidence_score"], p["latest_revision"])
        for p in response.json()
    ]
    assert resume == [
        (1, "to_review", pytest.approx(0.15), 1),
        (2, "validated", pytest.approx(1.0), 2),
        (3, "untranscribed", None, None),
    ]


def test_la_liste_des_pages_garde_ce_que_l_evaluation_lit(
    app: FastAPI, document: Document, queue: FakeQueue
) -> None:
    """`make eval` lit `id` et `page_number` dans cette liste : ils doivent y rester."""
    page = page_ocr(document, 1)
    client = build_client(app, FakeSession(document, [page]), queue)

    (corps,) = client.get(f"/documents/{document.id}/pages").json()

    assert corps["id"] == str(page.id)
    assert corps["page_number"] == 1
    assert corps["bulk_validated"] is False


def test_les_champs_de_la_galerie_n_ont_pas_de_valeur_par_defaut() -> None:
    """Une route qui oublierait de les calculer doit échouer, pas afficher « à relire »."""
    for champ in ("state", "confidence_score", "latest_revision", "bulk_validated"):
        assert PageSummary.model_fields[champ].is_required(), champ


def test_la_liste_des_pages_d_un_document_inconnu_donne_404(app: FastAPI, queue: FakeQueue) -> None:
    client = build_client(app, FakeSession(None, []), queue)

    assert client.get(f"/documents/{uuid4()}/pages").status_code == 404


def test_le_detail_d_une_page_dit_si_une_revision_a_ete_validee_en_lot(
    app: FastAPI, document: Document, queue: FakeQueue
) -> None:
    page = page_ocr(document, 1)
    client = build_client(app, FakeSession(document, [page]), queue)

    corps = client.get(f"/pages/{page.id}").json()

    assert corps["transcriptions"][0]["bulk_validated"] is False


# --- Validation groupée --------------------------------------------------------


def test_la_validation_groupee_valide_chaque_page_restante(
    app: FastAPI, document: Document, queue: FakeQueue
) -> None:
    premiere, seconde = page_ocr(document, 1), page_ocr(document, 2)
    client = build_client(app, FakeSession(document, [premiere, seconde]), queue)

    response = client.post(
        f"/documents/{document.id}/validate",
        json={"expected_revisions": affiche(premiere, seconde)},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "validated_pages": [1, 2],
        "already_validated": 0,
        "document_status": "validated",
    }
    for page in (premiere, seconde):
        derniere = page.transcriptions[-1]
        assert (derniere.revision, derniere.is_validated, derniere.bulk_validated) == (
            2,
            True,
            True,
        )
        assert page.transcriptions[0].origin is TranscriptionOrigin.OCR


def test_la_validation_groupee_n_enfile_qu_une_indexation(
    app: FastAPI, document: Document, queue: FakeQueue
) -> None:
    pages = [page_ocr(document, numero) for numero in (1, 2, 3)]
    session = FakeSession(document, pages)
    client = build_client(app, session, queue)

    client.post(f"/documents/{document.id}/validate", json={"expected_revisions": affiche(*pages)})

    assert queue.enqueued == [("index_document", (str(document.id),))]
    assert [obj.kind for obj in session.added if isinstance(obj, Job)] == ["index"]


def test_les_pages_deja_validees_sont_comptees_sans_etre_revalidees(
    app: FastAPI, document: Document, queue: FakeQueue
) -> None:
    relue, a_relire = page_validee(document, 1), page_ocr(document, 2)
    client = build_client(app, FakeSession(document, [relue, a_relire]), queue)

    corps = client.post(
        f"/documents/{document.id}/validate",
        json={"expected_revisions": affiche(relue, a_relire)},
    ).json()

    assert corps["validated_pages"] == [2]
    assert corps["already_validated"] == 1
    assert len(relue.transcriptions) == 2


def test_un_document_deja_indexe_ne_repart_pas_en_indexation(
    app: FastAPI, document: Document, queue: FakeQueue
) -> None:
    """Rien à valider : ni révision, ni réindexation, ni retour en `validated`."""
    document.status = DocumentStatus.INDEXED
    relue = page_validee(document, 1)
    client = build_client(app, FakeSession(document, [relue]), queue)

    response = client.post(
        f"/documents/{document.id}/validate", json={"expected_revisions": affiche(relue)}
    )

    assert response.status_code == 200, response.text
    assert response.json()["validated_pages"] == []
    assert response.json()["document_status"] == "indexed"
    assert queue.enqueued == []


def test_une_page_modifiee_depuis_l_affichage_fait_tout_refuser(
    app: FastAPI, document: Document, queue: FakeQueue
) -> None:
    premiere, seconde = page_ocr(document, 1), page_ocr(document, 2)
    vu = affiche(premiere, seconde)
    seconde.transcriptions.append(revision(seconde.id, 2, origine=TranscriptionOrigin.HUMAN))
    session = FakeSession(document, [premiere, seconde])
    client = build_client(app, session, queue)

    response = client.post(f"/documents/{document.id}/validate", json={"expected_revisions": vu})

    assert response.status_code == 409
    assert "page 2" in response.json()["detail"]
    assert len(premiere.transcriptions) == 1, "aucune page ne doit être validée"
    assert session.flushed is False
    assert queue.enqueued == []
    assert document.status is DocumentStatus.AWAITING_VALIDATION


@pytest.mark.parametrize(
    "statut",
    [
        DocumentStatus.NEW,
        DocumentStatus.PREPROCESSED,
        DocumentStatus.TRANSCRIBING,
        DocumentStatus.FAILED,
    ],
)
def test_un_document_qui_n_attend_pas_de_relecture_est_refuse(
    app: FastAPI, document: Document, queue: FakeQueue, statut: DocumentStatus
) -> None:
    """Pendant un OCR, des pages restent à lire : valider maintenant les laisserait de côté."""
    document.status = statut
    page = page_ocr(document, 1)
    client = build_client(app, FakeSession(document, [page]), queue)

    response = client.post(
        f"/documents/{document.id}/validate", json={"expected_revisions": affiche(page)}
    )

    assert response.status_code == 409
    assert statut.value in response.json()["detail"]
    assert len(page.transcriptions) == 1


def test_deux_validations_simultanees_la_seconde_est_refusee_sans_500(
    app: FastAPI, document: Document, queue: FakeQueue
) -> None:
    """Deux onglets confirment en même temps : la contrainte d'unicité tranche."""

    class SessionConcurrente(FakeSession):
        async def flush(self) -> None:
            raise IntegrityError("INSERT", {}, Exception("uq_revision_per_page"))

    page = page_ocr(document, 1)
    client = build_client(app, SessionConcurrente(document, [page]), queue)

    response = client.post(
        f"/documents/{document.id}/validate", json={"expected_revisions": affiche(page)}
    )

    assert response.status_code == 409
    assert "recharger" in response.json()["detail"]
    assert queue.enqueued == []


def test_la_validation_groupee_d_un_document_inconnu_donne_404(
    app: FastAPI, queue: FakeQueue
) -> None:
    client = build_client(app, FakeSession(None, []), queue)

    response = client.post(f"/documents/{uuid4()}/validate", json={"expected_revisions": {}})

    assert response.status_code == 404


def test_la_validation_groupee_exige_les_revisions_affichees(
    app: FastAPI, document: Document, queue: FakeQueue
) -> None:
    client = build_client(app, FakeSession(document, [page_ocr(document, 1)]), queue)

    assert client.post(f"/documents/{document.id}/validate", json={}).status_code == 422
