"""Évaluation de la recherche et de la réponse générée."""

import pytest

from scriptoria.evaluation.questions import rang_premiere_page, rappel_a_k, reponse_contient


def hits(*pages: int) -> list[dict]:
    return [{"page_number": page} for page in pages]


def test_le_rang_est_celui_de_la_premiere_page_attendue() -> None:
    assert rang_premiere_page(hits(4, 19, 20), {20, 19}) == 2


def test_aucune_page_attendue_donne_un_rang_absent() -> None:
    assert rang_premiere_page(hits(4, 5), {20}) is None


def test_rappel_a_k() -> None:
    rangs = [1, 3, None, 6]

    assert rappel_a_k(rangs, 1) == pytest.approx(0.25)
    assert rappel_a_k(rangs, 5) == pytest.approx(0.5)


def test_rappel_sans_question_est_refuse() -> None:
    with pytest.raises(ValueError, match="aucune question"):
        rappel_a_k([], 5)


@pytest.mark.parametrize(
    "reponse",
    [
        "Le capital assuré est de 7 700 000 francs [1].",
        "Il s'élève à 7.700.000 F.",
        "sept millions sept cent mille francs",
    ],
)
def test_une_valeur_acceptee_est_reconnue_quel_que_soit_son_format(reponse: str) -> None:
    valeurs = ("7.700.000", "sept millions sept cent mille")

    assert reponse_contient(reponse, valeurs) is not None


def test_une_valeur_voisine_n_est_pas_acceptee() -> None:
    assert reponse_contient("Il s'élève à 7 000 000 francs.", ("7.700.000",)) is None


def test_la_comparaison_ignore_la_casse() -> None:
    assert reponse_contient("Me Jean DUPONT", ("jean dupont",)) == "jean dupont"
