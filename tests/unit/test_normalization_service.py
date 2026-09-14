"""Révision « mise en forme » : ce qui s'écrit, et ce qui ne s'écrit pas.

La mise en forme **ajoute** une révision : la révision `ocr` reste la sortie brute
du modèle, ce qui permettra de rejouer une mise en forme améliorée sans repayer
l'OCR (~57 s par page). Ces tests portent sur la fonction pure qui prépare les
révisions ; le branchement à la base est couvert par les tests d'intégration.
"""

from collections.abc import Sequence
from uuid import uuid4

from scriptoria.db.models import ConfidenceBlock, Page, Transcription
from scriptoria.domain.enums import TranscriptionOrigin
from scriptoria.services.confidence import (
    METHOD_ARITHMETIC,
    METHOD_DOUBLE_PASS,
    METHOD_STRUCTURAL,
    SCORE_UNCERTAIN_LINE_BREAK,
    analyse_markdown,
)
from scriptoria.services.normalization import prepare_normalized_revisions
from scriptoria.ui.presentation import SEUIL_ALERTE

DOCUMENT = uuid4()
MODELE = "qwen2.5vl:7b"


def page(
    numero: int,
    texte: str,
    origine: TranscriptionOrigin = TranscriptionOrigin.OCR,
    blocs: Sequence[ConfidenceBlock] = (),
    validee: bool = False,
) -> Page:
    cible = Page(
        id=uuid4(), document_id=DOCUMENT, page_number=numero, raw_image_path=f"inbox/{numero}.png"
    )
    cible.transcriptions = [
        Transcription(
            page_id=cible.id,
            revision=1,
            content_markdown=texte,
            origin=origine,
            model_name=MODELE if origine is TranscriptionOrigin.OCR else None,
            is_validated=validee,
            bulk_validated=False,
            confidence_blocks=list(blocs),
        )
    ]
    return cible


def extraits(revision: Transcription, methode: str) -> list[str]:
    return [
        revision.content_markdown[bloc.start_offset : bloc.end_offset]
        for bloc in revision.confidence_blocks
        if bloc.method == methode
    ]


# --- Ce qui s'écrit ---------------------------------------------------------


def test_une_page_transcrite_recoit_une_revision_mise_en_forme() -> None:
    cible = page(1, "lequel contrat-\nne contenait,rien")

    (nouvelle,) = prepare_normalized_revisions([cible], known_texts=["contrat ne"])

    assert nouvelle.page_id == cible.id
    assert nouvelle.revision == 2
    assert nouvelle.origin is TranscriptionOrigin.NORMALIZED
    assert nouvelle.content_markdown == "lequel contrat ne contenait, rien"


def test_la_mise_en_forme_n_est_jamais_validee_d_office() -> None:
    """Comme l'OCR : la validation reste un geste humain."""
    (nouvelle,) = prepare_normalized_revisions([page(1, "un,deux")])

    assert nouvelle.is_validated is False
    assert nouvelle.bulk_validated is False
    assert nouvelle.model_name is None


def test_la_revision_ocr_reste_intacte() -> None:
    cible = page(1, "un,deux")

    prepare_normalized_revisions([cible])

    assert [revision.content_markdown for revision in cible.transcriptions] == ["un,deux"]


# --- Ce qui ne s'écrit pas --------------------------------------------------


def test_une_page_relue_par_un_humain_n_est_pas_mise_en_forme() -> None:
    assert prepare_normalized_revisions([page(1, "un,deux", TranscriptionOrigin.HUMAN)]) == []


def test_une_page_deja_mise_en_forme_ne_l_est_pas_deux_fois() -> None:
    """Rejouable : relancer l'OCR d'un document repris ne doit rien dupliquer."""
    cible = page(1, "un,deux")
    cible.transcriptions.append(
        Transcription(
            page_id=cible.id,
            revision=2,
            content_markdown="un, deux",
            origin=TranscriptionOrigin.NORMALIZED,
            is_validated=False,
            bulk_validated=False,
        )
    )

    assert prepare_normalized_revisions([cible]) == []


def test_un_texte_deja_propre_ne_cree_pas_de_revision() -> None:
    assert prepare_normalized_revisions([page(1, "Un texte propre.")]) == []


def test_une_page_relue_sert_de_contexte_sans_etre_reecrite() -> None:
    """Le fragment reste en tête de la page suivante : on ne réécrit pas une relecture."""
    relue = page(1, "l'exis-", TranscriptionOrigin.HUMAN, validee=True)
    suivante = page(2, "tence de deux,trois")

    (nouvelle,) = prepare_normalized_revisions([relue, suivante], known_texts=["existence"])

    assert nouvelle.page_id == suivante.id
    assert nouvelle.content_markdown == "tence de deux, trois"


# --- Blocs de confiance -----------------------------------------------------


def test_une_alerte_de_double_passage_suit_le_texte_qu_elle_couvre() -> None:
    """Le second passage n'est pas conservé : son alerte ne peut pas être recalculée."""
    texte = "le prix-\nprincipal de 28,60 francs"
    debut = texte.index("28,60")
    alerte = ConfidenceBlock(
        start_offset=debut, end_offset=debut + 5, score=0.2, method=METHOD_DOUBLE_PASS
    )

    (nouvelle,) = prepare_normalized_revisions(
        [page(1, texte, blocs=[alerte])], known_texts=["prix principal"]
    )

    assert extraits(nouvelle, METHOD_DOUBLE_PASS) == ["28,60"]
    assert [bloc.score for bloc in nouvelle.confidence_blocks] == [0.2]


def test_une_alerte_sur_un_texte_retire_disparait() -> None:
    """Un bloc qui ne couvre plus rien surlignerait une zone vide."""
    alerte = ConfidenceBlock(start_offset=0, end_offset=3, score=0.5, method=METHOD_DOUBLE_PASS)
    pages = [page(4, "-3-\nun,deux"), page(5, "-4-\ntrois,quatre", blocs=[alerte])]

    revisions = prepare_normalized_revisions(pages)

    assert extraits(revisions[1], METHOD_DOUBLE_PASS) == []


def test_les_controles_gratuits_sont_recalcules_sans_doublon() -> None:
    texte = (
        "Facture,suite\n"
        "| Désignation | Qté | PU HT | Total HT |\n"
        "|---|---|---|---|\n"
        "| Papier | 24 | 4,50 | 108,00 |\n"
        "| Encre | 6 | 28,60 | 173,40 |\n"
    )
    blocs = [
        ConfidenceBlock(
            start_offset=bloc.start_offset,
            end_offset=bloc.end_offset,
            score=bloc.score,
            method=bloc.method,
        )
        for bloc in analyse_markdown(texte)
    ]

    (nouvelle,) = prepare_normalized_revisions([page(1, texte, blocs=blocs)])

    (ligne,) = extraits(nouvelle, METHOD_ARITHMETIC)
    assert "28,60" in ligne


def test_un_tiret_indecis_devient_une_alerte_a_relire() -> None:
    (nouvelle,) = prepare_normalized_revisions([page(1, "gens ma-\nrisés,ici")], known_texts=["ma"])

    assert extraits(nouvelle, METHOD_STRUCTURAL) == ["ma-\nrisés"]
    assert [bloc.score for bloc in nouvelle.confidence_blocks] == [SCORE_UNCERTAIN_LINE_BREAK]


def test_un_tiret_indecis_est_visible_dans_l_ecran_de_validation() -> None:
    """Vu dans le navigateur le 2026-09-14 : à 0,5 pile, les 17 tirets indécis du règlement
    n'apparaissaient ni dans la galerie ni dans les fragments à vérifier — l'UI ne
    signale qu'un score **strictement** sous son seuil. Signaler sans montrer, c'est taire.
    """
    assert SCORE_UNCERTAIN_LINE_BREAK < SEUIL_ALERTE
