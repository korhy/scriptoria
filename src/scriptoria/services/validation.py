"""Validation humaine : état d'une page, complétude d'un document, validation groupée.

Partagé par `POST /pages/{id}/corrections` (une page) et
`POST /documents/{id}/validate` (toutes) : les deux gestes doivent juger une page
validée de la même façon, et faire passer le document en `validated` par le même
chemin.

**La validation groupée ne valide que ce que le relecteur avait à l'écran.** Elle
reçoit la dernière révision affichée de chaque page ; une page transcrite,
corrigée ou ajoutée depuis fait tout refuser. Sans cela, un OCR relancé entre
l'affichage et le clic ferait approuver un texte que personne n'a vu.
"""

import logging
from collections.abc import Mapping, Sequence
from operator import attrgetter
from uuid import UUID

from arq.connections import ArqRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from scriptoria.db.models import ConfidenceBlock, Document, Job, Page, Transcription
from scriptoria.domain.enums import DocumentStatus, JobStatus, PageState, TranscriptionOrigin
from scriptoria.services.confidence import ConfidenceBlock as ScoredBlock
from scriptoria.services.confidence import aggregate_page_score
from scriptoria.workers import INDEX_TASK

logger = logging.getLogger(__name__)


class BulkValidationConflictError(Exception):
    """Ce que le relecteur a vu n'est plus ce qui est en base : rien n'est validé."""


def latest_revision(page: Page) -> Transcription | None:
    return max(page.transcriptions, key=attrgetter("revision"), default=None)


def page_score(page: Page) -> float | None:
    """Score de la dernière révision : celle que le relecteur a sous les yeux.

    Passe par `aggregate_page_score` plutôt que de recalculer un minimum ici :
    la règle « le pire bloc fait la page, jamais la moyenne » n'a qu'un seul
    endroit où vivre.
    """
    latest = latest_revision(page)
    if latest is None:
        return None
    return aggregate_page_score(
        [
            ScoredBlock(
                start_offset=block.start_offset,
                end_offset=block.end_offset,
                score=block.score,
                method=block.method,
            )
            for block in latest.confidence_blocks
        ]
    )


def is_page_validated(page: Page) -> bool:
    return any(transcription.is_validated for transcription in page.transcriptions)


def is_bulk_validated(page: Page) -> bool:
    """Validée, mais seulement en lot : aucune révision n'a été approuvée page à page."""
    validated = [revision for revision in page.transcriptions if revision.is_validated]
    return bool(validated) and all(revision.bulk_validated for revision in validated)


def page_state(page: Page) -> PageState:
    latest = latest_revision(page)
    if latest is None:
        return PageState.UNTRANSCRIBED
    if is_page_validated(page):
        return PageState.VALIDATED
    if latest.origin is TranscriptionOrigin.HUMAN:
        return PageState.DRAFT
    return PageState.TO_REVIEW


def _pages_label(numbers: Sequence[int]) -> str:
    if len(numbers) == 1:
        return f"page {numbers[0]}"
    return "pages " + ", ".join(str(number) for number in numbers)


def _check_displayed(pages: Sequence[Page], expected: Mapping[UUID, int]) -> None:
    if set(expected) != {page.id for page in pages}:
        raise BulkValidationConflictError(
            "la liste des pages a changé depuis l'affichage : recharger avant de valider."
        )

    latest = {page.id: latest_revision(page) for page in pages}
    untranscribed = [page.page_number for page in pages if latest[page.id] is None]
    if untranscribed:
        raise BulkValidationConflictError(
            f"{_pages_label(untranscribed)} sans transcription : lancer l'OCR ou saisir "
            "le texte avant de valider le document."
        )

    changed = [
        page.page_number
        for page in pages
        if (revision := latest[page.id]) is not None and revision.revision != expected[page.id]
    ]
    if changed:
        raise BulkValidationConflictError(
            f"{_pages_label(changed)} modifiée(s) depuis l'affichage : recharger pour "
            "voir ce qui a changé avant de valider."
        )


def _approve(source: Transcription) -> Transcription:
    """Révision `n+1` qui approuve le texte de `source` tel quel.

    Les blocs de confiance sont **recopiés** : le texte est identique, les offsets
    restent justes, et une page douteuse validée sans être lue garde son alerte.
    Des copies, pas les mêmes objets : les rattacher ici les retirerait à `source`.
    """
    return Transcription(
        page_id=source.page_id,
        revision=source.revision + 1,
        content_markdown=source.content_markdown,
        origin=TranscriptionOrigin.HUMAN,
        model_name=None,
        is_validated=True,
        bulk_validated=True,
        confidence_blocks=[
            ConfidenceBlock(
                start_offset=block.start_offset,
                end_offset=block.end_offset,
                score=block.score,
                method=block.method,
            )
            for block in source.confidence_blocks
        ],
    )


def prepare_bulk_validation(
    pages: Sequence[Page], expected: Mapping[UUID, int]
) -> list[Transcription]:
    """Révisions à ajouter pour valider toutes les pages du document, dans l'ordre.

    `expected` porte, par page, la dernière révision **affichée**. Lève
    `BulkValidationConflictError` si elle ne correspond plus à la base, ou si une page
    n'a jamais été transcrite. Les pages déjà validées sont laissées telles
    quelles. Ne modifie rien : les révisions rendues ne sont rattachées à aucune page.
    """
    _check_displayed(pages, expected)
    return [
        _approve(revision)
        for page in pages
        if not is_page_validated(page) and (revision := latest_revision(page)) is not None
    ]


async def validate_document_if_complete(
    session: AsyncSession, document: Document, queue: ArqRedis
) -> None:
    """Valide le document si chacune de ses pages l'est, puis enfile l'indexation.

    Un document devient valide parce que toutes ses pages l'ont été — une par
    une, ou toutes d'un coup par la validation groupée, qui ne fait qu'ajouter
    une révision validée à chacune.

    L'indexation est **enfilée**, pas exécutée : vectoriser 200 pages prend des
    minutes, ce n'est pas le travail d'une requête HTTP.
    """
    result = await session.execute(
        select(Page)
        .where(Page.document_id == document.id)
        .options(selectinload(Page.transcriptions))
    )
    pages = list(result.scalars().all())
    if not pages or not all(is_page_validated(page) for page in pages):
        return

    document.status = DocumentStatus.VALIDATED

    job = await queue.enqueue_job(INDEX_TASK, str(document.id))
    arq_job_id = getattr(job, "job_id", None)
    session.add(
        Job(
            document_id=document.id,
            kind="index",
            status=JobStatus.QUEUED,
            arq_job_id=arq_job_id,
        )
    )
    logger.info("document %s validé — indexation enfilée (job %s)", document.id, arq_job_id)
