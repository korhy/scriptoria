"""Schémas des documents."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from scriptoria.domain.enums import DocumentStatus


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_filename: str
    status: DocumentStatus
    page_count: int
    created_at: datetime
    updated_at: datetime


class JobAccepted(BaseModel):
    """Accusé d'enfilage.

    L'API enfile, le worker exécute : la réponse ne dit pas que le travail est
    fait, elle dit qu'il est en file. `arq_job_id` permet de le suivre.
    """

    document_id: UUID
    kind: str
    arq_job_id: str | None
