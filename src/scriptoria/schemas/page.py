"""Schémas des pages et de leurs transcriptions."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from scriptoria.domain.enums import TranscriptionOrigin


class ConfidenceBlockRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    start_offset: int
    end_offset: int
    score: float
    # Quelle méthode a produit ce score : la méthode définitive n'est pas tranchée,
    # on garde la trace pour pouvoir les comparer sur les mêmes documents.
    method: str


class TranscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: UUID
    revision: int
    content_markdown: str
    origin: TranscriptionOrigin
    model_name: str | None
    is_validated: bool
    created_at: datetime
    confidence_blocks: list[ConfidenceBlockRead] = Field(default_factory=list)


class PageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    page_number: int
    raw_image_path: str
    preprocessed_image_path: str | None


class TranscriptionCorrection(BaseModel):
    """Correction humaine.

    Ne modifie jamais la révision existante : l'API crée une révision `n+1`
    d'origine `human`. C'est ce qui préserve l'historique image → brut → corrigé.
    """

    content_markdown: str
    validate_now: bool = True
