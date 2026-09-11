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
