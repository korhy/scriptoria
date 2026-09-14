"""État d'une page et validation groupée — les règles, sans HTTP ni base.

La validation groupée est le seul geste qui valide des pages que personne n'a
forcément lues. Deux garde-fous en découlent, et c'est ici qu'ils se fixent :

- **on ne valide que ce que le relecteur avait à l'écran** : une page transcrite
  ou corrigée entre l'affichage et le clic fait tout refuser ;
- **la trace du geste reste** : la révision porte `bulk_validated`, et reprend
  les blocs de confiance du texte qu'elle approuve — sans quoi l'alerte d'une
  page douteuse disparaîtrait au moment même où on la valide sans la lire.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from scriptoria.db.models import ConfidenceBlock, Page, Transcription
from scriptoria.domain.enums import PageState, TranscriptionOrigin
from scriptoria.services.validation import (
    BulkValidationConflictError,
    is_bulk_validated,
    page_score,
    page_state,
    prepare_bulk_validation,
)

HUMAN = TranscriptionOrigin.HUMAN


def revision(
    page_id: UUID,
    numero: int = 1,
    *,
    origine: TranscriptionOrigin = TranscriptionOrigin.OCR,
    validee: bool = False,
    en_lot: bool = False,
    texte: str = "| Encre | 6 | 28,60 | 173,40 |",
    scores: tuple[float, ...] = (),
) -> Transcription:
    return Transcription(
        id=uuid4(),
        page_id=page_id,
        revision=numero,
        content_markdown=texte,
        origin=origine,
        model_name="qwen2.5vl:7b" if origine is TranscriptionOrigin.OCR else None,
        is_validated=validee,
        bulk_validated=en_lot,
        created_at=datetime.now(UTC),
        confidence_blocks=[
            ConfidenceBlock(start_offset=0, end_offset=10, score=score, method="arithmetic")
            for score in scores
        ],
    )


def page(numero: int = 1, *revisions: Transcription) -> Page:
    page_id = revisions[0].page_id if revisions else uuid4()
    nouvelle = Page(
        id=page_id,
        document_id=uuid4(),
        page_number=numero,
        raw_image_path=f"inbox/x/{numero:04d}.png",
        preprocessed_image_path=None,
    )
    nouvelle.transcriptions = list(revisions)
    return nouvelle


def page_ocr(numero: int = 1, *, scores: tuple[float, ...] = ()) -> Page:
    return page(numero, revision(uuid4(), scores=scores))


def attendu(*pages: Page) -> dict[UUID, int]:
    """Ce que le relecteur avait à l'écran : la dernière révision de chaque page."""
    return {p.id: p.transcriptions[-1].revision for p in pages}


# --- État d'une page ----------------------------------------------------------


def test_une_page_sans_transcription_est_a_transcrire() -> None:
    assert page_state(page()) is PageState.UNTRANSCRIBED


def test_une_page_lue_par_l_ocr_est_a_relire() -> None:
    assert page_state(page_ocr()) is PageState.TO_REVIEW


def test_une_correction_enregistree_sans_validation_est_un_brouillon() -> None:
    page_id = uuid4()
    brouillon = page(1, revision(page_id), revision(page_id, 2, origine=HUMAN))

    assert page_state(brouillon) is PageState.DRAFT


def test_une_page_portant_une_revision_validee_est_validee() -> None:
    """Même critère que le passage du document en `validated`."""
    page_id = uuid4()
    relue = page(1, revision(page_id), revision(page_id, 2, origine=HUMAN, validee=True))

    assert page_state(relue) is PageState.VALIDATED


def test_seule_une_validation_en_lot_marque_la_page_validee_en_lot() -> None:
    page_id = uuid4()
    en_lot = page(
        1,
        revision(page_id),
        revision(page_id, 2, origine=HUMAN, validee=True, en_lot=True),
    )
    # Des révisions neuves : rattacher celles d'`en_lot` à une autre page les lui
    # retirerait (relation SQLAlchemy).
    autre_id = uuid4()
    relue_ensuite = page(
        1,
        revision(autre_id),
        revision(autre_id, 2, origine=HUMAN, validee=True, en_lot=True),
        revision(autre_id, 3, origine=HUMAN, validee=True),
    )

    assert is_bulk_validated(en_lot) is True
    assert is_bulk_validated(relue_ensuite) is False
    assert is_bulk_validated(page_ocr()) is False


# --- Score ----------------------------------------------------------------------


def test_une_page_sans_transcription_n_a_pas_de_score() -> None:
    assert page_score(page()) is None


def test_le_score_est_le_pire_bloc_de_la_derniere_revision() -> None:
    page_id = uuid4()
    corrigee = page(
        1,
        revision(page_id, scores=(0.1,)),
        revision(page_id, 2, origine=HUMAN, scores=(0.9, 0.4)),
    )

    assert page_score(corrigee) == pytest.approx(0.4)


def test_une_revision_sans_bloc_vaut_un() -> None:
    assert page_score(page_ocr()) == pytest.approx(1.0)


# --- Validation groupée -----------------------------------------------------------


def test_chaque_page_a_valider_recoit_une_revision_suivante_validee_en_lot() -> None:
    premiere, seconde = page_ocr(1), page_ocr(2)

    nouvelles = prepare_bulk_validation([premiere, seconde], attendu(premiere, seconde))

    assert [(n.page_id, n.revision) for n in nouvelles] == [(premiere.id, 2), (seconde.id, 2)]
    for nouvelle in nouvelles:
        assert nouvelle.origin is HUMAN
        assert nouvelle.model_name is None
        assert nouvelle.is_validated is True
        assert nouvelle.bulk_validated is True


def test_le_texte_valide_est_celui_de_la_derniere_revision() -> None:
    page_id = uuid4()
    brouillon = page(
        1,
        revision(page_id, texte="lu par l'OCR"),
        revision(page_id, 2, origine=HUMAN, texte="corrigé à la main"),
    )

    (nouvelle,) = prepare_bulk_validation([brouillon], attendu(brouillon))

    assert nouvelle.content_markdown == "corrigé à la main"
    assert nouvelle.revision == 3


def test_rien_n_est_modifie_sur_les_revisions_existantes() -> None:
    """La préparation rend des révisions neuves : elle n'attache ni ne touche rien."""
    douteuse = page_ocr(scores=(0.15,))
    source = douteuse.transcriptions[0]

    prepare_bulk_validation([douteuse], attendu(douteuse))

    assert douteuse.transcriptions == [source]
    assert source.is_validated is False
    assert len(source.confidence_blocks) == 1


def test_l_alerte_d_une_page_douteuse_survit_a_sa_validation_en_lot() -> None:
    """Valider sans lire ne doit pas effacer le signal qui disait de lire."""
    douteuse = page_ocr(scores=(0.15,))

    (nouvelle,) = prepare_bulk_validation([douteuse], attendu(douteuse))

    blocs = [(b.start_offset, b.end_offset, b.score, b.method) for b in nouvelle.confidence_blocks]
    assert blocs == [(0, 10, 0.15, "arithmetic")]
    assert nouvelle.confidence_blocks[0] is not douteuse.transcriptions[0].confidence_blocks[0]


def test_une_page_deja_validee_est_laissee_telle_quelle() -> None:
    page_id = uuid4()
    relue = page(1, revision(page_id), revision(page_id, 2, origine=HUMAN, validee=True))
    a_relire = page_ocr(2)

    nouvelles = prepare_bulk_validation([relue, a_relire], attendu(relue, a_relire))

    assert [n.page_id for n in nouvelles] == [a_relire.id]


def test_un_document_entierement_valide_ne_produit_rien() -> None:
    relue = page(1, revision(uuid4(), validee=True))

    assert prepare_bulk_validation([relue], attendu(relue)) == []


def test_une_page_qui_a_change_depuis_l_affichage_fait_tout_refuser() -> None:
    """OCR relancé, correction dans un autre onglet : ce texte-là, personne ne l'a vu."""
    premiere, seconde = page_ocr(1), page_ocr(7)
    affiche = attendu(premiere, seconde)
    seconde.transcriptions.append(revision(seconde.id, 2, origine=HUMAN))

    with pytest.raises(BulkValidationConflictError, match="page 7"):
        prepare_bulk_validation([premiere, seconde], affiche)


def test_une_page_jamais_transcrite_fait_tout_refuser() -> None:
    """Valider une page vide la marquerait relue sans aucun texte."""
    transcrite, vide = page_ocr(1), page(3)

    with pytest.raises(BulkValidationConflictError, match="page 3"):
        prepare_bulk_validation([transcrite, vide], {transcrite.id: 1, vide.id: 0})


@pytest.mark.parametrize("ecart", ["page absente", "page en trop"])
def test_une_liste_de_pages_differente_de_celle_du_document_fait_tout_refuser(
    ecart: str,
) -> None:
    premiere, seconde = page_ocr(1), page_ocr(2)
    complet = attendu(premiere, seconde)
    affiche = attendu(premiere) if ecart == "page absente" else complet | {uuid4(): 1}

    with pytest.raises(BulkValidationConflictError, match="recharger"):
        prepare_bulk_validation([premiere, seconde], affiche)
