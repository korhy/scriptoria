"""Tâches du pipeline.

Chaque tâche est une étape autonome : prétraitement, OCR, indexation. Les
découpler permet de rejouer une étape seule — par exemple ré-OCRiser sans
re-scanner, ou réindexer sans ré-OCRiser.
"""

from typing import Any
from uuid import UUID


async def preprocess_document(ctx: dict[str, Any], document_id: UUID) -> None:
    """Nettoie toutes les pages d'un document (deskew, débruitage, contraste)."""
    raise NotImplementedError("Tâche de prétraitement — à implémenter")


async def transcribe_document(ctx: dict[str, Any], document_id: UUID) -> None:
    """Retranscrit chaque page en Markdown et calcule la confiance.

    Crée une révision 1 d'origine `ocr` par page, puis fait passer le document
    en `awaiting_validation`.
    """
    raise NotImplementedError("Tâche OCR — à implémenter")


async def index_document(ctx: dict[str, Any], document_id: UUID) -> None:
    """Découpe, vectorise et indexe les transcriptions validées d'un document."""
    raise NotImplementedError("Tâche d'indexation — à implémenter")
