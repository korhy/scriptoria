"""Vocabulaire du domaine.

Défini ici plutôt que dans `db/models.py` pour que les schémas d'API et les
services puissent l'utiliser sans importer la couche de persistance.
"""

from enum import StrEnum


class DocumentStatus(StrEnum):
    """Progression d'un document dans le pipeline."""

    NEW = "new"
    PREPROCESSING = "preprocessing"
    TRANSCRIBING = "transcribing"
    AWAITING_VALIDATION = "awaiting_validation"
    VALIDATED = "validated"
    INDEXED = "indexed"
    FAILED = "failed"


class TranscriptionOrigin(StrEnum):
    """Qui a produit une révision de transcription."""

    OCR = "ocr"
    HUMAN = "human"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
