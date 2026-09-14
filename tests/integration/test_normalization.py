"""Mise en forme sur la vraie stack.

Les tests unitaires préparent les révisions en mémoire. Ils ne voient ni une
valeur d'enum absente de Postgres (`NORMALIZED`), ni des pages rechargées sans
la révision OCR que le worker vient d'écrire, ni des blocs de confiance qui ne
seraient pas enregistrés avec leur révision.

La révision OCR est **insérée**, pas produite par le modèle : le résultat attendu
doit être exact, et la sortie de `qwen2.5vl` ne l'est pas d'un passage à l'autre.
Le document est supprimé à la fin.
"""

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import update

from scriptoria.config import get_settings
from scriptoria.db.models import ConfidenceBlock, Document, Transcription
from scriptoria.db.session import create_engine, create_sessionmaker
from scriptoria.domain.enums import DocumentStatus, TranscriptionOrigin
from scriptoria.scripts.normalize import normalize
from scriptoria.services.confidence import METHOD_DOUBLE_PASS

pytestmark = [pytest.mark.integration]

# Tiret de remplissage, virgule collée, lignes cassées au milieu d'une phrase.
TEXTE_OCR = (
    "lequel contrat-\n"
    "ne contenait aucune clause,ni réserve.\n"
    "Le prix de 28,60 francs\n"
    "ne change pas au contrat."
)
TEXTE_MIS_EN_FORME = (
    "lequel contrat ne contenait aucune clause, ni réserve.\n\n"
    "Le prix de 28,60 francs ne change pas au contrat."
)


async def inserer_revision_ocr(document_id: str, page_id: str) -> None:
    """Une page telle que l'OCR la laisse, avec une alerte du second passage sur `28,60`."""
    debut = TEXTE_OCR.index("28,60")
    engine = create_engine(get_settings())
    try:
        async with create_sessionmaker(engine)() as session:
            session.add(
                Transcription(
                    page_id=UUID(page_id),
                    revision=1,
                    content_markdown=TEXTE_OCR,
                    origin=TranscriptionOrigin.OCR,
                    model_name="insérée par le test",
                    is_validated=False,
                    confidence_blocks=[
                        ConfidenceBlock(
                            start_offset=debut,
                            end_offset=debut + len("28,60"),
                            score=0.2,
                            method=METHOD_DOUBLE_PASS,
                        )
                    ],
                )
            )
            await session.execute(
                update(Document)
                .where(Document.id == UUID(document_id))
                .values(status=DocumentStatus.AWAITING_VALIDATION)
            )
            await session.commit()
    finally:
        await engine.dispose()


@pytest.fixture
def document_transcrit(
    api: httpx.Client, base_accessible: None, importer, attendre
) -> Iterator[dict[str, Any]]:
    document = importer("mise-en-forme.png")
    # Après le prétraitement : le worker remettrait sinon le statut à `preprocessed`.
    attendre(document["id"], "preprocessed")
    galerie = api.get(f"/documents/{document['id']}/pages")
    assert galerie.status_code == 200, galerie.text
    (page,) = galerie.json()

    yield {"document_id": document["id"], "page_id": page["id"]}

    suppression = api.delete(f"/documents/{document['id']}")
    assert suppression.status_code in (204, 404), suppression.text


async def test_la_mise_en_forme_ajoute_une_revision_sans_toucher_a_l_ocr(
    api: httpx.Client, document_transcrit: dict[str, Any]
) -> None:
    await inserer_revision_ocr(document_transcrit["document_id"], document_transcrit["page_id"])

    assert await normalize(UUID(document_transcrit["document_id"])) == 1

    ocr, mise_en_forme = api.get(f"/pages/{document_transcrit['page_id']}").json()["transcriptions"]
    assert (ocr["revision"], ocr["origin"], ocr["content_markdown"]) == (1, "ocr", TEXTE_OCR)
    assert (mise_en_forme["revision"], mise_en_forme["origin"]) == (2, "normalized")
    assert mise_en_forme["content_markdown"] == TEXTE_MIS_EN_FORME
    assert mise_en_forme["is_validated"] is False


async def test_l_alerte_du_second_passage_est_enregistree_sur_le_texte_mis_en_forme(
    api: httpx.Client, document_transcrit: dict[str, Any]
) -> None:
    await inserer_revision_ocr(document_transcrit["document_id"], document_transcrit["page_id"])
    await normalize(UUID(document_transcrit["document_id"]))

    *_, mise_en_forme = api.get(f"/pages/{document_transcrit['page_id']}").json()["transcriptions"]
    extraits = [
        mise_en_forme["content_markdown"][bloc["start_offset"] : bloc["end_offset"]]
        for bloc in mise_en_forme["confidence_blocks"]
        if bloc["method"] == METHOD_DOUBLE_PASS
    ]
    assert extraits == ["28,60"]


async def test_la_mise_en_forme_se_rejoue_sans_rien_dupliquer(
    api: httpx.Client, document_transcrit: dict[str, Any]
) -> None:
    await inserer_revision_ocr(document_transcrit["document_id"], document_transcrit["page_id"])
    await normalize(UUID(document_transcrit["document_id"]))

    assert await normalize(UUID(document_transcrit["document_id"])) == 0

    (page,) = api.get(f"/documents/{document_transcrit['document_id']}/pages").json()
    assert (page["state"], page["latest_revision"]) == ("to_review", 2)
