"""Révision « mise en forme » : la sortie brute de l'OCR garde la sienne.

**Tranché le 2026-09-14.** Le modèle vision recopie la mise en ligne de la machine
à écrire (voir `services/layout.py`). Plutôt que de corriger sa sortie avant de
l'enregistrer, on ajoute une révision `n+1` d'origine `normalized` :

- la révision `ocr` reste ce que le modèle a lu — c'est elle qu'on mesure, et
  une mise en forme améliorée pourra être rejouée sans repayer ~57 s par page ;
- la révision mise en forme n'est **jamais validée d'office** : c'est elle que le
  relecteur a sous les yeux, et la validation reste un geste humain ;
- seule une page dont la dernière révision sort de l'OCR est mise en forme. Une
  relecture humaine ne se réécrit pas, et une page déjà mise en forme ne l'est
  pas deux fois : l'opération se rejoue sans rien dupliquer.
"""

import logging
from collections.abc import Iterable, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from scriptoria.db.models import ConfidenceBlock, Page, Transcription
from scriptoria.domain.enums import TranscriptionOrigin
from scriptoria.services.confidence import (
    METHOD_DOUBLE_PASS,
    METHOD_STRUCTURAL,
    SCORE_UNCERTAIN_LINE_BREAK,
    analyse_markdown,
)
from scriptoria.services.layout import NormalizedPage, PageText, normalize_pages
from scriptoria.services.text_edits import remap_span
from scriptoria.services.validation import latest_revision

logger = logging.getLogger(__name__)


def _blocks(source: Transcription, normalized: NormalizedPage) -> list[ConfidenceBlock]:
    """Blocs de confiance de la révision mise en forme.

    - Les contrôles gratuits (arithmétique, structure) sont **recalculés** sur le
      nouveau texte : les recopier en plus les compterait deux fois.
    - Les divergences du second passage sont **reportées** : le second passage
      n'est pas conservé, on ne saurait pas les recalculer. Une page douteuse ne
      doit pas perdre son alerte parce qu'on a recollé ses lignes.
    - Chaque tiret indécis devient une alerte à relire.
    """
    blocks = [
        ConfidenceBlock(
            start_offset=block.start_offset,
            end_offset=block.end_offset,
            score=block.score,
            method=block.method,
        )
        for block in analyse_markdown(normalized.text)
    ]

    source_length = len(source.content_markdown)
    for block in source.confidence_blocks:
        if block.method != METHOD_DOUBLE_PASS:
            continue
        start, end = remap_span(
            normalized.rewrite,
            min(block.start_offset, source_length),
            min(block.end_offset, source_length),
        )
        # Un bloc dont tout le texte a disparu (numéro de page) ne surlignerait rien.
        if start < end:
            blocks.append(
                ConfidenceBlock(
                    start_offset=start, end_offset=end, score=block.score, method=block.method
                )
            )

    blocks.extend(
        ConfidenceBlock(
            start_offset=start,
            end_offset=end,
            score=SCORE_UNCERTAIN_LINE_BREAK,
            method=METHOD_STRUCTURAL,
        )
        for start, end in normalized.uncertain
    )
    return blocks


def prepare_normalized_revisions(
    pages: Sequence[Page], known_texts: Iterable[str] = ()
) -> list[Transcription]:
    """Révisions mises en forme à ajouter, dans l'ordre des pages.

    Ne modifie rien : les révisions rendues ne sont rattachées à aucune page. Une
    page dont le texte ne change pas et ne porte aucun doute n'en reçoit pas.

    Args:
        pages: les pages du document, révisions et blocs de confiance chargés.
        known_texts: d'autres textes dont les mots enrichissent le lexique.

    Raises:
        LayoutError: une lettre ou un chiffre aurait changé ; rien n'est préparé.
    """
    latest = {page.page_number: latest_revision(page) for page in pages}
    by_number = {page.page_number: page for page in pages}
    texts = [
        PageText(
            page_number=number,
            text=revision.content_markdown,
            editable=revision.origin is TranscriptionOrigin.OCR,
        )
        for number, revision in latest.items()
        if revision is not None
    ]

    revisions: list[Transcription] = []
    for normalized in normalize_pages(texts, known_texts):
        source = latest[normalized.page_number]
        if source is None:
            continue
        if normalized.text == source.content_markdown and not normalized.uncertain:
            continue
        if not normalized.text.strip():
            # Une page qui ne portait que son numéro : la vider ne servirait personne.
            logger.warning("page %s : mise en forme vide, révision OCR conservée", source.page_id)
            continue
        revisions.append(
            Transcription(
                page_id=by_number[normalized.page_number].id,
                revision=source.revision + 1,
                content_markdown=normalized.text,
                origin=TranscriptionOrigin.NORMALIZED,
                model_name=None,
                is_validated=False,
                bulk_validated=False,
                confidence_blocks=_blocks(source, normalized),
            )
        )
    return revisions


async def normalize_document(session: AsyncSession, document_id: UUID) -> int:
    """Ajoute à la session les révisions mises en forme d'un document.

    Ne valide pas la transaction : c'est à l'appelant de le faire, pour que la
    mise en forme et le changement de statut du document passent ensemble.

    Retourne le nombre de révisions ajoutées.
    """
    result = await session.execute(
        select(Page)
        .where(Page.document_id == document_id)
        .order_by(Page.page_number)
        .options(selectinload(Page.transcriptions).selectinload(Transcription.confidence_blocks))
        # Le worker vient d'ajouter les révisions OCR par `page_id` : sans
        # rechargement, les pages déjà en session ne les verraient pas.
        .execution_options(populate_existing=True)
    )
    pages = list(result.scalars().all())

    # Les relectures validées enrichissent le lexique : leurs mots ont été lus
    # par un humain, césures déjà recollées.
    validated = await session.execute(
        select(Transcription.content_markdown).where(
            Transcription.is_validated, Transcription.bulk_validated.is_(False)
        )
    )
    revisions = prepare_normalized_revisions(pages, known_texts=validated.scalars().all())

    for revision in revisions:
        session.add(revision)
    logger.info("document %s : %s page(s) mise(s) en forme", document_id, len(revisions))
    return len(revisions)
