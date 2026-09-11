"""Documents : dépôt, suivi, déclenchement du pipeline.

`GET /documents` est implémenté : il prouve que le chaînage
HTTP → dépendance → session SQLAlchemy → Postgres fonctionne de bout en bout.
Le reste renvoie 501 tant que le pipeline n'est pas écrit — un 501 explicite
vaut mieux qu'une route absente ou qu'un succès simulé.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from scriptoria.api.deps import DbSession
from scriptoria.db.models import Document
from scriptoria.schemas.document import DocumentCreate, DocumentRead

router = APIRouter(prefix="/documents", tags=["documents"])


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


@router.post("", status_code=status.HTTP_501_NOT_IMPLEMENTED, summary="Importe un document")
async def create_document(payload: DocumentCreate) -> None:
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "Import non implémenté : nécessite le découpage en pages et l'écriture "
        "des images dans DATA_DIR/images (voir services/preprocessing.py).",
    )


@router.post(
    "/{document_id}/transcribe",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="Lance la retranscription OCR",
)
async def transcribe_document(document_id: UUID) -> None:
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "OCR non implémenté : voir services/ocr.py et workers/tasks.py.",
    )
