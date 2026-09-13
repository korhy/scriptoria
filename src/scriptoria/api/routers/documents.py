"""Documents : import, suivi, accès aux images.

L'import écrit les images puis **enfile** le prétraitement — il ne l'exécute pas.
Nettoyer une page prend de l'ordre de la seconde, un document en compte parfois
deux cents : ce n'est pas le travail d'une requête HTTP.
"""

import logging
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

import anyio
from arq.connections import ArqRedis
from arq.jobs import Job as ArqJob
from arq.jobs import JobStatus as ArqJobStatus
from elasticsearch import ApiError, TransportError
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from scriptoria.api.deps import AppSettings, DbSession, EsClient, TaskQueue
from scriptoria.db.models import Document, Job, Page, Transcription
from scriptoria.domain.enums import DocumentStatus, JobStatus, TranscriptionOrigin
from scriptoria.schemas.document import DocumentRead, JobAccepted
from scriptoria.schemas.page import PageRead
from scriptoria.services.storage import (
    MAX_PAGE_BYTES,
    MAX_PAGES_PER_DOCUMENT,
    UnsupportedImageError,
    raw_page_relpath,
    remove_document_files,
    validate_image_suffix,
    write_page_bytes,
)
from scriptoria.workers import PREPROCESS_TASK, TRANSCRIBE_TASK

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

_UPLOAD_CHUNK_BYTES = 1024 * 1024

# États arq d'un job qui peut encore écrire sur un document.
_ARQ_ACTIVE_STATUSES = frozenset(
    {ArqJobStatus.queued, ArqJobStatus.deferred, ArqJobStatus.in_progress}
)


async def _pages_transcribed(session: AsyncSession, document_ids: list[UUID]) -> dict[UUID, int]:
    """Pages portant au moins une révision OCR, par document — en une seule requête.

    Même critère que la reprise du worker (`_already_transcribed`) : l'origine,
    pas la simple présence d'une révision. Une page saisie à la main n'a jamais
    été OCRisée et ne compte pas. Un document absent du résultat en a zéro.
    """
    if not document_ids:
        return {}
    result = await session.execute(
        select(Page.document_id, func.count(distinct(Page.id)))
        .join(Transcription, Transcription.page_id == Page.id)
        .where(
            Page.document_id.in_(document_ids),
            Transcription.origin == TranscriptionOrigin.OCR,
        )
        .group_by(Page.document_id)
    )
    return {document_id: count for document_id, count in result.all()}


async def _last_job(session: AsyncSession, document_id: UUID) -> Job | None:
    """Le job le plus récent du document, quel qu'en soit le type."""
    result = await session.execute(
        select(Job).where(Job.document_id == document_id).order_by(Job.created_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


def _relaunch_refusal(last_job: Job | None) -> str | None:
    """Pourquoi un document en échec ne peut pas repartir en OCR — `None` s'il le peut.

    Seul un OCR en échec se relance : après un prétraitement raté, l'OCR
    transcrirait des images non nettoyées ; après une indexation ratée, c'est
    l'indexation qu'il faut rejouer. Et une relance déjà en file ne se double pas.
    """
    if last_job is None:
        return "document en échec sans job enregistré : impossible de dire quelle étape relancer."
    if last_job.kind != "transcribe":
        return (
            f"l'échec vient de l'étape '{last_job.kind}' : seul un OCR en échec peut être relancé."
        )
    if last_job.status != JobStatus.FAILED:
        return f"un OCR est déjà '{last_job.status.value}' pour ce document."
    return None


async def _arq_job_active(queue: ArqRedis, arq_job_id: str | None) -> bool:
    """arq tient-il encore ce job en file ou en cours ?

    La base ne suffit pas : un job peut y rester `running` longtemps après la mort
    de sa tâche — c'est ce que laissait le défaut d'annulation corrigé le
    2026-09-13. arq, lui, sait ce qui tourne. Sans identifiant, rien ne prouve que
    le job est mort : il est présumé actif.
    """
    if arq_job_id is None:
        return True
    return await ArqJob(arq_job_id, queue).status() in _ARQ_ACTIVE_STATUSES


def _to_read(document: Document, pages_transcribed: int) -> DocumentRead:
    return DocumentRead(
        id=document.id,
        source_filename=document.source_filename,
        status=document.status,
        page_count=document.page_count,
        pages_transcribed=pages_transcribed,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.get("", response_model=list[DocumentRead], summary="Liste les documents")
async def list_documents(
    session: DbSession, limit: int = 50, offset: int = 0
) -> list[DocumentRead]:
    result = await session.execute(
        select(Document).order_by(Document.created_at.desc()).limit(limit).offset(offset)
    )
    documents = list(result.scalars().all())
    counts = await _pages_transcribed(session, [document.id for document in documents])
    return [_to_read(document, counts.get(document.id, 0)) for document in documents]


@router.get("/{document_id}", response_model=DocumentRead, summary="Détail d'un document")
async def get_document(document_id: UUID, session: DbSession) -> DocumentRead:
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document introuvable")
    counts = await _pages_transcribed(session, [document_id])
    return _to_read(document, counts.get(document_id, 0))


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprime un document : index, base et fichiers",
)
async def delete_document(
    document_id: UUID,
    session: DbSession,
    es: EsClient,
    queue: TaskQueue,
    settings: AppSettings,
) -> None:
    """Retire un document de partout, dans un ordre qui ne ment jamais.

    1. **L'index d'abord.** Si la suite échoue, le document reste en base sans
       fragment et `make reindex` le rétablit. Dans l'ordre inverse, une panne
       d'Elasticsearch laisserait la recherche citer un document disparu.
    2. **La base ensuite**, validée ici même : la cascade des clés étrangères
       emporte pages, révisions, blocs de confiance et jobs.
    3. **Les fichiers en dernier**, une fois la base validée : un commit raté
       laisserait sinon un document privé de ses images.

    Refusé tant qu'arq tient un job du document en file ou en cours : supprimer
    sous un worker qui écrit ferait échouer sa tâche au milieu d'une page.
    """
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document introuvable")

    result = await session.execute(
        select(Job).where(
            Job.document_id == document_id,
            Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
    )
    for job in result.scalars().all():
        if await _arq_job_active(queue, job.arq_job_id):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"un job '{job.kind}' est encore '{job.status.value}' pour ce document : "
                "attendre sa fin avant de le supprimer.",
            )

    try:
        await es.delete_by_query(
            index=settings.elasticsearch_index,
            query={"term": {"document_id": str(document_id)}},
            # Index absent : il n'y a rien à retirer, ce n'est pas une panne.
            ignore_unavailable=True,
            # Une recherche lancée juste après ne doit plus trouver le document.
            refresh=True,
            conflicts="proceed",
        )
    except (ApiError, TransportError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Elasticsearch n'a pas retiré les fragments du document ({exc}) : "
            "rien n'a été supprimé.",
        ) from exc

    await session.execute(delete(Document).where(Document.id == document_id))
    await session.commit()

    # Suppression disque bloquante : déportée dans un thread.
    await anyio.to_thread.run_sync(remove_document_files, settings.data_dir, document_id)
    logger.info("document %s supprimé : index, base et fichiers", document_id)


@router.get("/{document_id}/pages", response_model=list[PageRead], summary="Pages d'un document")
async def list_pages(document_id: UUID, session: DbSession) -> list[Page]:
    result = await session.execute(
        select(Page).where(Page.document_id == document_id).order_by(Page.page_number)
    )
    pages = list(result.scalars().all())
    if not pages and await session.get(Document, document_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document introuvable")
    return pages


@router.get(
    "/{document_id}/pages/{page_number}/image",
    response_class=FileResponse,
    summary="Image d'une page (brute ou prétraitée)",
)
async def get_page_image(
    document_id: UUID,
    page_number: int,
    session: DbSession,
    settings: AppSettings,
    variant: Literal["raw", "preprocessed"] = "preprocessed",
) -> FileResponse:
    result = await session.execute(
        select(Page).where(Page.document_id == document_id, Page.page_number == page_number)
    )
    page = result.scalar_one_or_none()
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page introuvable")

    relative = page.raw_image_path if variant == "raw" else page.preprocessed_image_path
    if not relative:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "image prétraitée pas encore produite : le prétraitement est-il terminé ?",
        )

    path = (settings.data_dir / relative).resolve()
    # Les chemins viennent de la base, donc de nous — mais une base altérée ne
    # doit pas pouvoir faire servir un fichier arbitraire de la machine.
    if not path.is_relative_to(settings.data_dir.resolve()) or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "fichier absent")
    return FileResponse(path)


async def _read_bounded(upload: UploadFile) -> bytes:
    """Lit un fichier téléversé en refusant de dépasser la borne.

    La lecture est fragmentée : contrôler la taille après coup supposerait
    d'avoir déjà tout accepté en mémoire.
    """
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(_UPLOAD_CHUNK_BYTES):
        total += len(chunk)
        if total > MAX_PAGE_BYTES:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"page trop lourde ({upload.filename!r}) : limite {MAX_PAGE_BYTES} octets.",
            )
        chunks.append(chunk)

    if not chunks:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"fichier vide : {upload.filename!r}")
    return b"".join(chunks)


@router.post(
    "",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Importe un document (une image par page, dans l'ordre)",
)
async def create_document(
    session: DbSession,
    settings: AppSettings,
    queue: TaskQueue,
    files: Annotated[list[UploadFile], File(description="Pages, dans l'ordre de lecture")],
) -> DocumentRead:
    if len(files) > MAX_PAGES_PER_DOCUMENT:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"{len(files)} pages : maximum {MAX_PAGES_PER_DOCUMENT} par document.",
        )

    # Tout valider avant d'écrire quoi que ce soit : un import à moitié accepté
    # laisserait des fichiers sans document.
    try:
        suffixes = [validate_image_suffix(upload.filename) for upload in files]
    except UnsupportedImageError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    document_id = uuid4()
    document = Document(
        id=document_id,
        source_filename=Path(files[0].filename or "").name,
        status=DocumentStatus.NEW,
        page_count=len(files),
    )
    session.add(document)

    try:
        for page_number, (upload, suffix) in enumerate(zip(files, suffixes, strict=True), start=1):
            payload = await _read_bounded(upload)
            relative = raw_page_relpath(document_id, page_number, suffix)
            # Écriture disque bloquante : déportée dans un thread pour ne pas
            # figer la boucle d'événements pendant l'import.
            await anyio.to_thread.run_sync(write_page_bytes, settings.data_dir / relative, payload)
            session.add(
                Page(
                    document_id=document_id,
                    page_number=page_number,
                    raw_image_path=str(relative),
                )
            )

        await session.flush()

        job = await queue.enqueue_job(PREPROCESS_TASK, str(document_id))
        session.add(
            Job(
                document_id=document_id,
                kind="preprocess",
                status=JobStatus.QUEUED,
                arq_job_id=getattr(job, "job_id", None),
            )
        )
        await session.flush()
        # Recharge les valeurs calculées par la base (created_at, updated_at).
        await session.refresh(document)
    except Exception:
        # La transaction sera annulée ; les fichiers déjà écrits, eux, resteraient.
        await anyio.to_thread.run_sync(remove_document_files, settings.data_dir, document_id)
        raise

    logger.info("document %s importé (%s pages), prétraitement enfilé", document_id, len(files))
    # Aucune transcription ne peut exister pour un document qui vient de naître.
    return _to_read(document, 0)


@router.post(
    "/{document_id}/transcribe",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enfile la retranscription OCR des pages d'un document",
)
async def transcribe_document(
    document_id: UUID,
    session: DbSession,
    queue: TaskQueue,
) -> JobAccepted:
    """Met l'OCR en file. Ne transcrit rien : une page coûte de l'ordre de la minute.

    Exige un document `preprocessed`, ou `failed` au cours d'un OCR. OCRiser une
    image non nettoyée dépenserait des heures pour un résultat dégradé, et relancer
    un document déjà en cours ferait tourner deux modèles vision à la fois sur une
    machine de 16 Go.

    **Relance après échec** : le worker saute les pages qui portent déjà une
    révision OCR. Un lot interrompu à la 180ᵉ page reprend à la 181ᵉ —
    `pages_transcribed` dit où.
    """
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document introuvable")

    if document.status == DocumentStatus.FAILED:
        refusal = _relaunch_refusal(await _last_job(session, document_id))
        if refusal is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, refusal)
    elif document.status != DocumentStatus.PREPROCESSED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"document en statut '{document.status.value}' : l'OCR attend un document "
            f"'{DocumentStatus.PREPROCESSED.value}'.",
        )

    job = await queue.enqueue_job(TRANSCRIBE_TASK, str(document_id))
    arq_job_id = getattr(job, "job_id", None)
    session.add(
        Job(
            document_id=document_id,
            kind="transcribe",
            status=JobStatus.QUEUED,
            arq_job_id=arq_job_id,
        )
    )
    await session.flush()

    logger.info("document %s : OCR enfilé (job %s)", document_id, arq_job_id)
    return JobAccepted(document_id=document_id, kind="transcribe", arq_job_id=arq_job_id)
