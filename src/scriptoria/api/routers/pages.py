"""Révisions de transcription — surface consommée par l'UI de validation.

La lecture des pages et de leurs images passe par `/documents/{id}/pages` :
une page n'existe jamais hors d'un document, la hiérarchie le reflète.

**Une correction crée une révision, elle n'écrase jamais.** Aucune route de ce
module ne modifie le texte d'une révision existante : corriger insère une
révision `n+1` d'origine `human`. C'est ce qui préserve l'historique
image → texte brut → texte corrigé, et ce qui permettra de mesurer la qualité de
l'OCR sur des cas réels.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from scriptoria.api.deps import DbSession, TaskQueue
from scriptoria.db.models import Document, Job, Page, Transcription
from scriptoria.domain.enums import DocumentStatus, JobStatus, TranscriptionOrigin
from scriptoria.schemas.page import PageDetail, RevisionCreated, TranscriptionCorrection
from scriptoria.services.confidence import ConfidenceBlock, aggregate_page_score
from scriptoria.workers import INDEX_TASK

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pages", tags=["pages"])


async def _load_page(session: DbSession, page_id: UUID) -> Page | None:
    """Charge une page avec ses révisions et leurs blocs, en une requête."""
    result = await session.execute(
        select(Page)
        .where(Page.id == page_id)
        .options(selectinload(Page.transcriptions).selectinload(Transcription.confidence_blocks))
    )
    return result.scalar_one_or_none()


def _page_score(page: Page) -> float | None:
    """Score de la dernière révision : celle que le relecteur a sous les yeux.

    Passe par `aggregate_page_score` plutôt que de recalculer un minimum ici :
    la règle « le pire bloc fait la page, jamais la moyenne » n'a qu'un seul
    endroit où vivre.
    """
    if not page.transcriptions:
        return None

    latest = page.transcriptions[-1]
    return aggregate_page_score(
        [
            ConfidenceBlock(
                start_offset=block.start_offset,
                end_offset=block.end_offset,
                score=block.score,
                method=block.method,
            )
            for block in latest.confidence_blocks
        ]
    )


async def _validate_document_if_complete(
    session: DbSession, document: Document, queue: TaskQueue
) -> None:
    """Valide le document si chacune de ses pages l'est, puis enfile l'indexation.

    Il n'existe pas de validation globale : un document devient valide parce que
    toutes ses pages l'ont été, une par une.

    L'indexation est **enfilée**, pas exécutée : vectoriser 200 pages prend des
    minutes, ce n'est pas le travail d'une requête HTTP.
    """
    result = await session.execute(
        select(Page)
        .where(Page.document_id == document.id)
        .options(selectinload(Page.transcriptions))
    )
    pages = list(result.scalars().all())
    toutes_validees = bool(pages) and all(
        any(transcription.is_validated for transcription in page.transcriptions) for page in pages
    )
    if not toutes_validees:
        return

    document.status = DocumentStatus.VALIDATED

    job = await queue.enqueue_job(INDEX_TASK, str(document.id))
    arq_job_id = getattr(job, "job_id", None)
    session.add(
        Job(
            document_id=document.id,
            kind="index",
            status=JobStatus.QUEUED,
            arq_job_id=arq_job_id,
        )
    )
    logger.info("document %s validé — indexation enfilée (job %s)", document.id, arq_job_id)


@router.get("/{page_id}", response_model=PageDetail, summary="Détail d'une page")
async def get_page(page_id: UUID, session: DbSession) -> PageDetail:
    page = await _load_page(session, page_id)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page introuvable")

    detail = PageDetail.model_validate(page)
    return detail.model_copy(update={"confidence_score": _page_score(page)})


@router.post(
    "/{page_id}/corrections",
    response_model=RevisionCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Enregistre une correction humaine (crée une révision n+1)",
)
async def correct_page(
    page_id: UUID,
    payload: TranscriptionCorrection,
    session: DbSession,
    queue: TaskQueue,
) -> RevisionCreated:
    """Insère une révision `n+1` d'origine `human`. Ne modifie jamais la précédente.

    Valider sans rien changer passe par la même route : c'est aussi une révision,
    et c'est ce qui datera l'accord du relecteur.
    """
    page = await _load_page(session, page_id)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page introuvable")

    document = await session.get(Document, page.document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document introuvable")

    revision = max((existing.revision for existing in page.transcriptions), default=0) + 1
    transcription = Transcription(
        page_id=page.id,
        revision=revision,
        content_markdown=payload.content_markdown,
        origin=TranscriptionOrigin.HUMAN,
        # Aucun modèle : c'est un humain qui a écrit ce texte.
        model_name=None,
        is_validated=payload.validate_now,
    )
    # Ajout par la relation et non par `session.add` : le contrôle de complétude
    # qui suit relit ces collections, et doit y voir la révision qu'on vient
    # d'écrire.
    page.transcriptions.append(transcription)
    await session.flush()

    if payload.validate_now:
        await _validate_document_if_complete(session, document, queue)

    logger.info(
        "page %s : révision %s enregistrée (validée=%s)", page.id, revision, payload.validate_now
    )
    return RevisionCreated(
        page_id=page.id,
        revision=revision,
        origin=TranscriptionOrigin.HUMAN,
        is_validated=payload.validate_now,
        document_status=document.status,
    )
