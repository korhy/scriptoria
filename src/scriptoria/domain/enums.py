"""Vocabulaire du domaine.

Défini ici plutôt que dans `db/models.py` pour que les schémas d'API et les
services puissent l'utiliser sans importer la couche de persistance.
"""

from enum import StrEnum


class DocumentStatus(StrEnum):
    """Progression d'un document dans le pipeline."""

    NEW = "new"
    PREPROCESSING = "preprocessing"
    # Images nettoyées, en attente d'OCR. État distinct de TRANSCRIBING : tant
    # que l'OCR n'est pas écrit, un document s'arrête ici — le dire explicitement
    # vaut mieux que de le laisser dans un état suggérant un travail en cours.
    PREPROCESSED = "preprocessed"
    TRANSCRIBING = "transcribing"
    AWAITING_VALIDATION = "awaiting_validation"
    VALIDATED = "validated"
    INDEXED = "indexed"
    FAILED = "failed"


class TranscriptionOrigin(StrEnum):
    """Qui a produit une révision de transcription."""

    OCR = "ocr"
    HUMAN = "human"


class PageState(StrEnum):
    """Où en est la relecture d'une page — ce qu'affiche chaque vignette de l'UI.

    `validated` suit le critère du document : une page l'est dès qu'**une** de ses
    révisions a été validée, même si un brouillon plus récent a suivi.
    """

    UNTRANSCRIBED = "untranscribed"
    # Dernière révision sortie de l'OCR, que personne n'a encore approuvée.
    TO_REVIEW = "to_review"
    # Correction humaine enregistrée sans validation.
    DRAFT = "draft"
    VALIDATED = "validated"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
