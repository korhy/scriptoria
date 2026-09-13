"""Construction du rapport d'évaluation, sans toucher à la stack.

Le rapport est ce qu'on lira pour trancher : il doit distinguer « faux »,
« juste » et « non mesuré ». Un taux d'erreur à 0 faute de référence, ou une
page absente comptée comme juste, ferait prendre une absence de mesure pour un
bon résultat.
"""

from pathlib import Path

import pytest

from scriptoria.evaluation.corpus import (
    ControleNombres,
    ControleTables,
    Corpus,
    Question,
    TableAttendue,
)
from scriptoria.evaluation.rapport import (
    ResultatQuestion,
    construire_rapport,
    evaluer_transcriptions,
    rapport_markdown,
)


def corpus(references: dict[int, str] | None = None, **champs: object) -> Corpus:
    valeurs: dict[object, object] = {
        "nom": "test",
        "dossier": Path("/corpus/test"),
        "pages": tuple(Path(f"page-{n:02d}.jpg") for n in range(1, 4)),
        "references": references or {},
        "controles_tables": (),
        "controles_nombres": (),
        "questions": (),
    }
    valeurs.update(champs)
    return Corpus(**valeurs)  # type: ignore[arg-type]


# --- Transcription ----------------------------------------------------------


def test_sans_reference_le_taux_d_erreur_est_non_mesure() -> None:
    resultat = evaluer_transcriptions(corpus(), {1: "texte", 2: "texte"})

    assert resultat["cer_global"] is None
    assert resultat["pages"] == []


def test_le_taux_global_est_pondere_par_la_longueur_des_pages() -> None:
    """Une page courte parfaite ne doit pas masquer une page longue ratée."""
    references = {1: "abcd", 2: "abcdefghijklmnop"}
    textes = {1: "abcd", 2: "abcdefghXXXXXXXX"}

    resultat = evaluer_transcriptions(corpus(references), textes)

    assert resultat["cer_global"] == pytest.approx(8 / 20)
    assert [page["page"] for page in resultat["pages"]] == [1, 2]


def test_une_page_de_reference_non_transcrite_est_listee_a_part() -> None:
    resultat = evaluer_transcriptions(corpus({1: "abcd", 2: "efgh"}), {1: "abcd"})

    assert resultat["references_sans_transcription"] == [2]
    assert resultat["cer_global"] == 0.0


def test_les_nombres_sont_agreges_sur_les_pages_de_reference() -> None:
    references = {1: "lot 70/2.000", 2: "prix 2.250.000 et 32.700"}
    textes = {1: "lot 70/2.000", 2: "prix 2.250.000 et 32.100"}

    resultat = evaluer_transcriptions(corpus(references), textes)

    assert resultat["nombres"] == {
        "attendus": 3,
        "lus": 3,
        "justes": 2,
        "rappel": pytest.approx(2 / 3),
        "precision": pytest.approx(2 / 3),
    }


# --- Rapport complet --------------------------------------------------------


def question(identifiant: str, rang: int | None, trouvee: str | None) -> ResultatQuestion:
    return ResultatQuestion(
        id=identifiant,
        question=f"question {identifiant}",
        pages_attendues=(2,),
        pages_retrouvees=(2,) if rang else (3,),
        rang=rang,
        reponse="réponse" if trouvee else "rien",
        valeur_trouvee=trouvee,
    )


def rapport_complet() -> dict:
    controles = corpus(
        controles_tables=(ControleTables("tantièmes", 1, 2, 1000, (TableAttendue("A", 2, 1000),)),),
        controles_nombres=(ControleNombres("prix", 3, ("2.250.000",)),),
        questions=(Question("q1", "?", (2,), ("x",)), Question("q2", "?", (2,), ("y",))),
    )
    textes = {1: "600/1.000", 2: "400/1.000 1.000/1.000", 3: "prix 2.250.000"}
    return construire_rapport(
        controles,
        textes,
        [question("q1", 1, "x"), question("q2", None, None)],
        configuration={"max_edge_px": 1600},
        durees={"ocr": 12.5},
    )


def test_le_rapport_agrege_controles_et_questions() -> None:
    rapport = rapport_complet()

    assert rapport["controles"]["tables"][0]["juste"] is True
    assert rapport["controles"]["nombres"][0]["juste"] is True
    assert rapport["controles"]["justes"] == 2
    assert rapport["controles"]["total"] == 2
    assert rapport["recherche"]["rappel_a_1"] == pytest.approx(0.5)
    assert rapport["recherche"]["reponses_exactes"] == pytest.approx(0.5)
    assert rapport["configuration"] == {"max_edge_px": 1600}


def test_sans_question_la_recherche_est_non_mesuree() -> None:
    rapport = construire_rapport(corpus(), {1: "texte"}, [], configuration={}, durees={})

    assert rapport["recherche"] is None


def test_le_markdown_distingue_juste_faux_et_non_mesure() -> None:
    texte = rapport_markdown(rapport_complet())

    assert "non mesuré" in texte
    assert "✓" in texte
    assert "✗" in texte
    assert "q2" in texte
