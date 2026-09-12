"""Schémas des pages et de leurs transcriptions."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from scriptoria.domain.enums import DocumentStatus, TranscriptionOrigin


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


class PageDetail(PageRead):
    """Page, ses révisions et leurs blocs de confiance — la vue de validation.

    `confidence_score` porte sur la **dernière** révision : c'est celle que le
    relecteur a sous les yeux. Il permet de trier les pages par urgence de
    relecture plutôt que de les parcourir dans l'ordre.
    """

    transcriptions: list[TranscriptionRead] = Field(default_factory=list)
    confidence_score: float | None = None


class TranscriptionCorrection(BaseModel):
    """Correction humaine.

    Ne modifie jamais la révision existante : l'API crée une révision `n+1`
    d'origine `human`. C'est ce qui préserve l'historique image → brut → corrigé.

    Le texte est nettoyé de ses espaces de bord puis refusé s'il ne reste rien :
    valider une page avec un contenu vide effacerait la transcription tout en
    marquant la page comme relue.
    """

    content_markdown: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    validate_now: bool = True


class RevisionCreated(BaseModel):
    """Accusé de création d'une révision.

    `document_status` évite un aller-retour à l'UI : elle a besoin de savoir si
    cette validation était la dernière attendue pour le document.
    """

    page_id: UUID
    revision: int
    origin: TranscriptionOrigin
    is_validated: bool
    document_status: DocumentStatus
