"""Références d'évaluation : pages saisies en fichier, ou relues dans l'UI.

Une relecture validée est une référence : c'est le texte qu'un humain a approuvé.
Mais elle part du texte de l'OCR, et l'œil y laisse passer ce qu'une saisie
aurait vu. Une saisie prime donc toujours, et chaque référence garde son origine.
"""

from scriptoria.evaluation.references import (
    RELECTURE,
    SAISIE,
    Reference,
    derniere_revision,
    fusionner_references,
)


def revision(
    numero: int, origine: str, texte: str, validee: bool = True, en_lot: bool = False
) -> dict:
    return {
        "revision": numero,
        "origin": origine,
        "content_markdown": texte,
        "is_validated": validee,
        "bulk_validated": en_lot,
    }


def test_la_derniere_revision_est_la_plus_haute_quel_que_soit_l_ordre() -> None:
    revisions = [revision(3, "human", "v3"), revision(1, "ocr", "ocr"), revision(2, "human", "v2")]

    assert derniere_revision(revisions, origine="human") == "v3"


def test_une_relecture_non_validee_n_est_pas_une_reference() -> None:
    """Un brouillon enregistré sans validation n'engage pas le relecteur."""
    revisions = [
        revision(1, "ocr", "ocr", validee=False),
        revision(2, "human", "validée"),
        revision(3, "human", "brouillon", validee=False),
    ]

    assert derniere_revision(revisions, origine="human", validee=True) == "validée"


def test_une_validation_en_lot_n_est_pas_une_reference() -> None:
    """Valider d'un clic n'est pas relire : ce texte d'OCR donnerait 0 % d'erreur."""
    revisions = [
        revision(1, "ocr", "ocr", validee=False),
        revision(2, "human", "relue une à une"),
        revision(3, "human", "brouillon", validee=False),
        revision(4, "human", "brouillon", en_lot=True),
    ]

    assert derniere_revision(revisions, origine="human", validee=True) == "relue une à une"


def test_une_page_seulement_validee_en_lot_n_a_pas_de_reference() -> None:
    revisions = [revision(1, "ocr", "ocr", validee=False), revision(2, "human", "ocr", en_lot=True)]

    assert derniere_revision(revisions, origine="human", validee=True) is None


def test_sans_revision_de_cette_origine_rien_n_est_rendu() -> None:
    assert derniere_revision([revision(1, "ocr", "ocr")], origine="human", validee=True) is None


def test_un_fichier_saisi_prime_sur_la_relecture_de_la_meme_page() -> None:
    references = fusionner_references({2: "saisie"}, {1: "relue", 2: "relue aussi"})

    assert dict(references) == {1: Reference("relue", RELECTURE), 2: Reference("saisie", SAISIE)}
    assert list(references) == [1, 2]


def test_une_relecture_vide_n_est_pas_une_reference() -> None:
    """Un taux d'erreur ne se calcule pas sur une référence vide."""
    assert dict(fusionner_references({}, {1: "  \n"})) == {}
