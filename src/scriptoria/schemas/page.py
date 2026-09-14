"""Schémas des pages et de leurs transcriptions."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, StringConstraints

from scriptoria.domain.enums import DocumentStatus, PageState, TranscriptionOrigin


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
    # Validée d'un clic avec tout le document : approuvée, pas forcément lue.
    bulk_validated: bool
    created_at: datetime
    confidence_blocks: list[ConfidenceBlockRead] = Field(default_factory=list)


class PageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    page_number: int
    raw_image_path: str
    preprocessed_image_path: str | None


class PageSummary(PageRead):
    """Une vignette de la galerie : où en est la page, et faut-il la lire d'abord.

    Aucun champ n'a de valeur par défaut : une route qui oublierait d'en calculer
    un doit échouer, pas afficher « à relire » sur une page validée.
    """

    state: PageState
    # Score de la dernière révision ; `None` pour une page jamais transcrite.
    confidence_score: float | None
    latest_revision: int | None
    bulk_validated: bool


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


class BulkValidationRequest(BaseModel):
    """Valider d'un geste toutes les pages restantes d'un document.

    `expected_revisions` porte, pour **chaque** page, la dernière révision que le
    relecteur avait à l'écran (0 pour une page sans transcription). Si la base a
    bougé depuis, rien n'est validé : on n'approuve pas un texte que personne n'a
    vu passer.
    """

    expected_revisions: dict[UUID, NonNegativeInt]


class BulkValidationResult(BaseModel):
    validated_pages: list[int]
    # Pages qui l'étaient déjà : laissées telles quelles, sans révision de plus.
    already_validated: int
    document_status: DocumentStatus
