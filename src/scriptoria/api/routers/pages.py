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
from scriptoria.db.models import Document, Page, Transcription
from scriptoria.domain.enums import TranscriptionOrigin
from scriptoria.schemas.page import PageDetail, RevisionCreated, TranscriptionCorrection
from scriptoria.services.validation import page_score, validate_document_if_complete

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


@router.get("/{page_id}", response_model=PageDetail, summary="Détail d'une page")
async def get_page(page_id: UUID, session: DbSession) -> PageDetail:
    page = await _load_page(session, page_id)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page introuvable")

    detail = PageDetail.model_validate(page)
    return detail.model_copy(update={"confidence_score": page_score(page)})


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
        await validate_document_if_complete(session, document, queue)

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
