"""Schémas des documents."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from scriptoria.domain.enums import DocumentStatus


class DocumentRead(BaseModel):
    """Un document et sa progression.

    `pages_transcribed` compte les pages portant une révision OCR : c'est ce qui
    distingue un lot de 200 pages qui avance d'un lot bloqué, tous deux en
    `transcribing` pendant des heures. **Il n'a pas de valeur par défaut** : un
    0 par défaut afficherait « aucune page » sur un lot déjà transcrit. Une route
    qui oublie de le calculer doit échouer, pas mentir.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_filename: str
    status: DocumentStatus
    page_count: int
    pages_transcribed: int
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
