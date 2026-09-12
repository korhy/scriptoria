"""Tâches du pipeline.

Chaque tâche est une étape autonome : prétraitement, OCR, indexation. Les
découpler permet de rejouer une étape seule — par exemple ré-OCRiser sans
re-scanner, ou réindexer sans ré-OCRiser.

Les tâches sont **idempotentes** : rejouer `preprocess_document` sur un document
déjà traité réécrit les mêmes fichiers aux mêmes chemins. C'est ce qui rend une
reprise après incident possible sans nettoyage préalable.
"""

import logging
from itertools import batched
from pathlib import Path
from typing import Any
from uuid import UUID

import anyio
import httpx
from elasticsearch import AsyncElasticsearch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from scriptoria.config import Settings
from scriptoria.db.models import ConfidenceBlock as ConfidenceBlockRow
from scriptoria.db.models import Document, Job, Page, Transcription
from scriptoria.domain.enums import DocumentStatus, JobStatus, TranscriptionOrigin
from scriptoria.services.chunking import Chunk, chunk_markdown
from scriptoria.services.confidence import (
    ConfidenceBlock,
    aggregate_page_score,
    analyse_markdown,
    compare_passes,
)
from scriptoria.services.embeddings import embed_texts
from scriptoria.services.indexing import (
    IndexingError,
    ensure_index,
    index_chunks,
    validate_embedding_dim,
)
from scriptoria.services.ocr import OcrResult, transcribe_page
from scriptoria.services.preprocessing import preprocess_page
from scriptoria.services.storage import preprocessed_page_relpath

logger = logging.getLogger(__name__)

# Vectoriser 200 pages en un seul appel ferait déborder les 16 Go de la machine.
# Un lot de 16 fragments reste confortable et amortit les allers-retours HTTP.
EMBEDDING_BATCH_SIZE = 16


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


async def _transcribe_with_confidence(
    client: httpx.AsyncClient,
    settings: Settings,
    image: Path,
) -> tuple[OcrResult, list[ConfidenceBlock]]:
    """Transcrit une page et en dérive ses blocs de confiance.

    Les contrôles gratuits (cohérence arithmétique, structure) s'appliquent à
    toutes les pages. Le second passage, lui, ne se déclenche que si ces
    contrôles ont déjà fait tomber le score : il coûte ~57 s de plus, soit trois
    heures supplémentaires sur un lot de 200 pages s'il était systématique.

    Le passage conservé est toujours le premier — température nulle, donc le plus
    fidèle ; le second ne sert qu'à mesurer la stabilité de la lecture.
    """
    result = await transcribe_page(client, image, settings.ollama_vision_model)
    blocks = analyse_markdown(result.content_markdown)

    score = aggregate_page_score(blocks)
    if score >= settings.confidence_second_pass_threshold:
        return result, blocks

    logger.info("page %s : score %.2f — second passage de vérification", image.name, score)
    second = await transcribe_page(
        client,
        image,
        settings.ollama_vision_model,
        temperature=settings.confidence_second_pass_temperature,
    )
    return result, blocks + compare_passes(result.content_markdown, second.content_markdown)


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
                result, blocks = await _transcribe_with_confidence(
                    client, settings, settings.data_dir / relative
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
                        # Rattachés à la révision : un bloc n'a de sens que
                        # relativement au texte dont il donne les offsets.
                        confidence_blocks=[
                            ConfidenceBlockRow(
                                start_offset=block.start_offset,
                                end_offset=block.end_offset,
                                score=block.score,
                                method=block.method,
                            )
                            for block in blocks
                        ],
                    )
                )
                # Validation immédiate : c'est ce qui rend la reprise possible.
                await session.commit()
                transcribed += 1
                logger.info(
                    "document %s page %s transcrite en %.1fs (révision %s, "
                    "confiance %.2f sur %s bloc(s))",
                    doc_id,
                    page.page_number,
                    result.duration_seconds,
                    revision,
                    aggregate_page_score(blocks),
                    len(blocks),
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


def _latest_validated(page: Page) -> Transcription | None:
    """Dernière révision validée d'une page, s'il y en a une.

    « Validée », pas « la plus récente » : une correction en cours d'écriture ne
    doit pas se retrouver dans l'index.
    """
    validees = [
        transcription for transcription in page.transcriptions if transcription.is_validated
    ]
    if not validees:
        return None
    return max(validees, key=lambda transcription: transcription.revision)


def _collect_chunks(pages: list[Page], doc_id: UUID) -> tuple[list[Chunk], list[int]]:
    """Fragments issus des révisions validées, et numéros des pages qui n'en ont pas."""
    chunks: list[Chunk] = []
    unvalidated: list[int] = []

    for page in pages:
        validated = _latest_validated(page)
        if validated is None:
            unvalidated.append(page.page_number)
            continue
        chunks.extend(chunk_markdown(validated.content_markdown, doc_id, page.page_number))

    return chunks, unvalidated


async def index_document(ctx: dict[str, Any], document_id: str) -> int:
    """Découpe, vectorise et indexe les transcriptions validées d'un document.

    Retourne le nombre de fragments écrits — `make reindex` s'en sert pour dire
    ce qu'il a reconstruit.

    Idempotent : l'identifiant ES d'un fragment dérive de sa position (document,
    page), si bien qu'un second passage écrase au lieu de dupliquer. C'est ce qui
    permet de réindexer un document corrigé, et de rejouer `make reindex` sans
    vider l'index au préalable.

    N'indexe **que** les révisions validées. Un document dont une page n'a pas
    été relue n'est pas indexé à moitié : la tâche échoue en nommant la page.
    Un index à moitié rempli qui se présente comme complet est pire qu'une
    absence d'index.
    """
    settings: Settings = ctx["settings"]
    factory: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    es: AsyncElasticsearch = ctx["es"]
    client: httpx.AsyncClient = ctx["ollama"]
    doc_id = UUID(document_id)
    written = 0

    try:
        async with factory() as session:
            document = await session.get(Document, doc_id)
            if document is None:
                logger.warning("index_document: document %s introuvable", doc_id)
                return 0

            await _update_job(session, doc_id, "index", JobStatus.RUNNING)
            await session.commit()

            result_pages = await session.execute(
                select(Page)
                .where(Page.document_id == doc_id)
                .order_by(Page.page_number)
                .options(selectinload(Page.transcriptions))
            )
            chunks, unvalidated = _collect_chunks(list(result_pages.scalars().all()), doc_id)
            if unvalidated:
                raise IndexingError(
                    f"document {doc_id} : page(s) {unvalidated} sans révision validée. "
                    "Indexer un document à moitié relu le ferait passer pour complet."
                )

            # Avant toute vectorisation : inutile de payer des embeddings si
            # l'index ne peut pas être créé.
            await ensure_index(es, settings.elasticsearch_index, settings.embedding_dim)

            for batch in batched(chunks, EMBEDDING_BATCH_SIZE):
                lot = list(batch)
                vectors = await embed_texts(
                    client, [chunk.content for chunk in lot], settings.ollama_embedding_model
                )
                validate_embedding_dim(vectors, settings.embedding_dim)
                written += await index_chunks(es, settings.elasticsearch_index, lot, vectors)

            document.status = DocumentStatus.INDEXED
            await _update_job(session, doc_id, "index", JobStatus.DONE)
            await session.commit()

        logger.info("document %s indexé (%s fragment(s))", doc_id, written)
        return written

    except Exception as exc:
        logger.exception("indexation du document %s en échec", doc_id)
        async with factory() as session:
            document = await session.get(Document, doc_id)
            if document is not None:
                document.status = DocumentStatus.FAILED
            await _update_job(session, doc_id, "index", JobStatus.FAILED, error=str(exc))
            await session.commit()
        raise
