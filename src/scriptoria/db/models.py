"""Schéma relationnel du pipeline.

Deux invariants structurent ce modèle :

1. **Postgres est la source de vérité.** Elasticsearch n'est qu'un index
   reconstructible : tout ce qui est indexé doit pouvoir être rejoué depuis ici.
2. **Une correction n'écrase jamais.** Valider ou corriger une transcription crée
   une révision `n+1` dans `transcriptions`. L'historique image → texte brut →
   texte corrigé reste intégralement consultable, ce qui permet plus tard de
   mesurer la qualité de l'OCR sur des cas réels.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from scriptoria.domain.enums import DocumentStatus, JobStatus, TranscriptionOrigin

__all__ = [
    "Base",
    "ConfidenceBlock",
    "Document",
    "DocumentStatus",
    "Job",
    "JobStatus",
    "Page",
    "Transcription",
    "TranscriptionOrigin",
]


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status"),
        default=DocumentStatus.NEW,
        nullable=False,
        index=True,
    )
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    pages: Mapped[list["Page"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Page.page_number"
    )


class Page(Base, TimestampMixin):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number", name="uq_page_per_document"),)

    id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Chemins relatifs à DATA_DIR — les fichiers eux-mêmes vivent hors base.
    raw_image_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    preprocessed_image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    document: Mapped[Document] = relationship(back_populates="pages")
    transcriptions: Mapped[list["Transcription"]] = relationship(
        back_populates="page", cascade="all, delete-orphan", order_by="Transcription.revision"
    )


class Transcription(Base):
    """Une révision du texte d'une page. Immuable une fois créée."""

    __tablename__ = "transcriptions"
    __table_args__ = (UniqueConstraint("page_id", "revision", name="uq_revision_per_page"),)

    id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Commence à 1. Une correction humaine crée n+1, ne modifie jamais n.
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[TranscriptionOrigin] = mapped_column(
        Enum(TranscriptionOrigin, name="transcription_origin"), nullable=False
    )
    # Modèle Ollama utilisé pour une révision d'origine OCR ; NULL si humaine.
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Seule la dernière révision validée est indexée dans Elasticsearch.
    is_validated: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    page: Mapped[Page] = relationship(back_populates="transcriptions")
    confidence_blocks: Mapped[list["ConfidenceBlock"]] = relationship(
        back_populates="transcription", cascade="all, delete-orphan"
    )


class ConfidenceBlock(Base):
    """Score de confiance sur un fragment de transcription.

    `method` est volontairement une chaîne libre : la méthode définitive de calcul
    n'est pas tranchée (déclaratif du LLM, double passage, perplexité, Tesseract
    en secours). Conserver la méthode employée permet de les comparer sur les
    mêmes documents plutôt que d'en choisir une à l'aveugle.
    """

    __tablename__ = "confidence_blocks"

    id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    transcription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transcriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Offsets de caractères dans `content_markdown`.
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)

    transcription: Mapped[Transcription] = relationship(back_populates="confidence_blocks")


class Job(Base, TimestampMixin):
    """Suivi d'une tâche asynchrone (prétraitement, OCR, indexation)."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"), default=JobStatus.QUEUED, nullable=False, index=True
    )
    arq_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
