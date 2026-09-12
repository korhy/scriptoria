"""Tâches du pipeline.

Chaque tâche est une étape autonome : prétraitement, OCR, indexation. Les
découpler permet de rejouer une étape seule — par exemple ré-OCRiser sans
re-scanner, ou réindexer sans ré-OCRiser.

Les tâches sont **idempotentes** : rejouer `preprocess_document` sur un document
déjà traité réécrit les mêmes fichiers aux mêmes chemins. C'est ce qui rend une
reprise après incident possible sans nettoyage préalable.
"""

import logging
from typing import Any
from uuid import UUID

import anyio
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from scriptoria.config import Settings
from scriptoria.db.models import Document, Job, Page, Transcription
from scriptoria.domain.enums import DocumentStatus, JobStatus, TranscriptionOrigin
from scriptoria.services.ocr import transcribe_page
from scriptoria.services.preprocessing import preprocess_page
from scriptoria.services.storage import preprocessed_page_relpath

logger = logging.getLogger(__name__)


async def _update_job(
    session: AsyncSession,
    document_id: UUID,
    kind: str,
    status: JobStatus,
    error: str | None = None,
) -> None:
    """Met à jour le dernier job de ce type pour ce document, s'il existe."""
    result = await session.execute(
        select(Job)
        .where(Job.document_id == document_id, Job.kind == kind)
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    job = result.scalar_one_or_none()
    if job is None:
        return
    job.status = status
    if error is not None:
        job.error = error


async def preprocess_document(ctx: dict[str, Any], document_id: str) -> None:
    """Nettoie toutes les pages d'un document (deskew, débruitage, contraste).

    L'identifiant transite en chaîne : la sérialisation de la file ne doit pas
    imposer de contrainte sur les types du domaine.

    Le document termine en `PREPROCESSED`, pas en `TRANSCRIBING` : l'OCR n'est
    pas implémenté, et prétendre le contraire rendrait l'état illisible.
    """
    settings: Settings = ctx["settings"]
    factory: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    doc_id = UUID(document_id)
    page_count = 0

    try:
        async with factory() as session:
            document = await session.get(Document, doc_id)
            if document is None:
                # Document supprimé entre la mise en file et l'exécution : ce
                # n'est pas une erreur, mais il faut le dire.
                logger.warning("preprocess_document: document %s introuvable", doc_id)
                return

            document.status = DocumentStatus.PREPROCESSING
            await _update_job(session, doc_id, "preprocess", JobStatus.RUNNING)
            await session.commit()

            result_pages = await session.execute(
                select(Page).where(Page.document_id == doc_id).order_by(Page.page_number)
            )
            pages = list(result_pages.scalars().all())
            page_count = len(pages)

            for page in pages:
                source = settings.data_dir / page.raw_image_path
                relative = preprocessed_page_relpath(doc_id, page.page_number)

                # OpenCV est bloquant et gourmand : l'exécuter dans la boucle
                # d'événements figerait le worker et ses battements de cœur.
                result = await anyio.to_thread.run_sync(
                    preprocess_page, source, settings.data_dir / relative
                )

                page.preprocessed_image_path = str(relative)
                logger.info(
                    "document %s page %s: inclinaison %+.2f° — %s",
                    doc_id,
                    page.page_number,
                    result.deskew_angle,
                    " → ".join(result.steps_applied),
                )

            document.status = DocumentStatus.PREPROCESSED
            await _update_job(session, doc_id, "preprocess", JobStatus.DONE)
            await session.commit()

        logger.info("document %s prétraité (%s pages)", doc_id, page_count)

    except Exception as exc:
        logger.exception("prétraitement du document %s en échec", doc_id)
        # Session neuve : celle de la transaction annulée n'est plus utilisable.
        async with factory() as session:
            document = await session.get(Document, doc_id)
            if document is not None:
                document.status = DocumentStatus.FAILED
            await _update_job(session, doc_id, "preprocess", JobStatus.FAILED, error=str(exc))
            await session.commit()
        # Relancée pour qu'arq enregistre l'échec au lieu de le croire réussi.
        raise


def _already_transcribed(page: Page) -> bool:
    """Une révision d'origine OCR existe déjà pour cette page.

    C'est le critère de reprise : il porte sur l'origine, pas sur la simple
    présence d'une révision — une page saisie à la main n'a jamais été OCRisée.
    """
    return any(
        transcription.origin is TranscriptionOrigin.OCR for transcription in page.transcriptions
    )


async def transcribe_document(ctx: dict[str, Any], document_id: str) -> None:
    """Retranscrit chaque page en Markdown via le modèle vision.

    Crée une révision d'origine `ocr` par page, puis fait passer le document en
    `awaiting_validation` : une transcription automatique n'est jamais validée
    d'office, la validation est un geste humain.

    **Reprenable.** Une page transcrite est validée en base avant de passer à la
    suivante, et une page portant déjà une révision OCR est ignorée. Sur la
    machine cible une page coûte ~57 s : un lot de 200 pages tourne trois heures,
    et un incident à la 180ᵉ page ne doit pas rejouer les 179 premières.
    """
    settings: Settings = ctx["settings"]
    factory: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    client: httpx.AsyncClient = ctx["ollama"]
    doc_id = UUID(document_id)
    transcribed = 0
    skipped = 0

    try:
        async with factory() as session:
            document = await session.get(Document, doc_id)
            if document is None:
                logger.warning("transcribe_document: document %s introuvable", doc_id)
                return

            document.status = DocumentStatus.TRANSCRIBING
            await _update_job(session, doc_id, "transcribe", JobStatus.RUNNING)
            await session.commit()

            result_pages = await session.execute(
                select(Page)
                .where(Page.document_id == doc_id)
                .order_by(Page.page_number)
                # Les révisions servent au test de reprise : les charger ici
                # évite une requête par page dans la boucle.
                .options(selectinload(Page.transcriptions))
            )
            pages = list(result_pages.scalars().all())

            for page in pages:
                if _already_transcribed(page):
                    skipped += 1
                    logger.info(
                        "document %s page %s déjà transcrite — ignorée", doc_id, page.page_number
                    )
                    continue

                # L'image nettoyée si elle existe : c'est elle qui a été
                # redimensionnée, et la résolution domine le coût de l'OCR.
                # À défaut, l'originale — mieux vaut transcrire que sauter.
                relative = page.preprocessed_image_path or page.raw_image_path
                result = await transcribe_page(
                    client, settings.data_dir / relative, settings.ollama_vision_model
                )

                revision = (
                    max(
                        (transcription.revision for transcription in page.transcriptions),
                        default=0,
                    )
                    + 1
                )
                session.add(
                    Transcription(
                        page_id=page.id,
                        revision=revision,
                        content_markdown=result.content_markdown,
                        origin=TranscriptionOrigin.OCR,
                        model_name=result.model_name,
                        is_validated=False,
                    )
                )
                # Validation immédiate : c'est ce qui rend la reprise possible.
                await session.commit()
                transcribed += 1
                logger.info(
                    "document %s page %s transcrite en %.1fs (révision %s)",
                    doc_id,
                    page.page_number,
                    result.duration_seconds,
                    revision,
                )

            document.status = DocumentStatus.AWAITING_VALIDATION
            await _update_job(session, doc_id, "transcribe", JobStatus.DONE)
            await session.commit()

        logger.info(
            "document %s transcrit (%s pages, %s déjà faites)", doc_id, transcribed, skipped
        )

    except Exception as exc:
        logger.exception("transcription du document %s en échec", doc_id)
        # Session neuve : celle de la transaction annulée n'est plus utilisable.
        # Les pages déjà transcrites, elles, sont validées et seront sautées au
        # prochain passage.
        async with factory() as session:
            document = await session.get(Document, doc_id)
            if document is not None:
                document.status = DocumentStatus.FAILED
            await _update_job(session, doc_id, "transcribe", JobStatus.FAILED, error=str(exc))
            await session.commit()
        raise


async def index_document(ctx: dict[str, Any], document_id: str) -> None:
    """Découpe, vectorise et indexe les transcriptions validées d'un document."""
    raise NotImplementedError("Tâche d'indexation — voir services/indexing.py")
