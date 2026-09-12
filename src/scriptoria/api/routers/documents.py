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
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select

from scriptoria.api.deps import AppSettings, DbSession, TaskQueue
from scriptoria.db.models import Document, Job, Page
from scriptoria.domain.enums import DocumentStatus, JobStatus
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

_UPLOAD_CHUNK_BYTES = 1024 * 1024

PREPROCESS_TASK = "preprocess_document"
TRANSCRIBE_TASK = "transcribe_document"


@router.get("", response_model=list[DocumentRead], summary="Liste les documents")
async def list_documents(session: DbSession, limit: int = 50, offset: int = 0) -> list[Document]:
    result = await session.execute(
        select(Document).order_by(Document.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


@router.get("/{document_id}", response_model=DocumentRead, summary="Détail d'un document")
async def get_document(document_id: UUID, session: DbSession) -> Document:
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document introuvable")
    return document


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
) -> Document:
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
    return document


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

    Exige un document `preprocessed`. OCRiser une image non nettoyée dépenserait
    des heures pour un résultat dégradé, et relancer un document déjà en cours
    ferait tourner deux modèles vision à la fois sur une machine de 16 Go.
    """
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document introuvable")

    if document.status != DocumentStatus.PREPROCESSED:
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
