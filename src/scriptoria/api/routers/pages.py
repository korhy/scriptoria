"""Pages et révisions de transcription — surface consommée par l'UI de validation."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from scriptoria.schemas.page import TranscriptionCorrection

router = APIRouter(prefix="/pages", tags=["pages"])


@router.get("/{page_id}", status_code=status.HTTP_501_NOT_IMPLEMENTED, summary="Détail d'une page")
async def get_page(page_id: UUID) -> None:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "à implémenter avec le pipeline")


@router.get(
    "/{page_id}/image",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="Image de la page (brute ou prétraitée)",
)
async def get_page_image(page_id: UUID, preprocessed: bool = False) -> None:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "à implémenter avec le pipeline")


@router.post(
    "/{page_id}/corrections",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="Enregistre une correction humaine (crée une révision n+1)",
)
async def correct_page(page_id: UUID, payload: TranscriptionCorrection) -> None:
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "Correction non implémentée. Invariant à respecter : créer une révision "
        "n+1 d'origine `human`, ne jamais modifier la révision existante.",
    )
