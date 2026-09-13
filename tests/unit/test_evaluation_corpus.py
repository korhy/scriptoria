"""Chargement d'un corpus d'évaluation.

Un corpus mal formé doit échouer tout de suite et dire pourquoi : découvrir après
quarante minutes d'OCR qu'une page manquait fausserait tout le passage.
"""

from pathlib import Path

import pytest

from scriptoria.evaluation.corpus import CorpusError, charger_corpus


def creer_corpus(racine: Path, pages: int = 3) -> Path:
    (racine / "pages").mkdir(parents=True)
    for numero in range(1, pages + 1):
        (racine / "pages" / f"page-{numero:02d}.jpg").write_bytes(b"jpeg")
    return racine


def test_les_pages_sont_rangees_par_numero(tmp_path: Path) -> None:
    dossier = creer_corpus(tmp_path, pages=12)

    corpus = charger_corpus(dossier)

    assert [page.name for page in corpus.pages][:3] == ["page-01.jpg", "page-02.jpg", "page-03.jpg"]
    assert len(corpus.pages) == 12


def test_une_page_manquante_est_refusee(tmp_path: Path) -> None:
    dossier = creer_corpus(tmp_path, pages=3)
    (dossier / "pages" / "page-02.jpg").unlink()

    with pytest.raises(CorpusError, match="page 2"):
        charger_corpus(dossier)


def test_un_corpus_sans_page_est_refuse(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="pages"):
        charger_corpus(tmp_path)


def test_les_references_saisies_sont_chargees_et_le_lisezmoi_ignore(tmp_path: Path) -> None:
    dossier = creer_corpus(tmp_path)
    (dossier / "reference").mkdir()
    (dossier / "reference" / "page-02.md").write_text("Texte de la page deux", encoding="utf-8")
    (dossier / "reference" / "LISEZMOI.md").write_text("conventions", encoding="utf-8")

    corpus = charger_corpus(dossier)

    assert dict(corpus.references) == {2: "Texte de la page deux"}


def test_une_reference_vide_est_refusee(tmp_path: Path) -> None:
    """Un fichier créé mais pas encore saisi ne doit pas passer pour une page blanche."""
    dossier = creer_corpus(tmp_path)
    (dossier / "reference").mkdir()
    (dossier / "reference" / "page-01.md").write_text("  \n", encoding="utf-8")

    with pytest.raises(CorpusError, match=r"page-01\.md"):
        charger_corpus(dossier)


def test_les_controles_et_questions_sont_charges(tmp_path: Path) -> None:
    dossier = creer_corpus(tmp_path)
    (dossier / "controles.toml").write_text(
        """
[[tables]]
nom = "Tantièmes généraux"
pages = [1, 2]
denominateur = 2000
lots = [{ nom = "Généraux", lots = 59, total = 2000 }]

[[nombres]]
nom = "Prix"
page = 3
nombres = ["2.250.000", "32.700"]
""",
        encoding="utf-8",
    )
    (dossier / "questions.toml").write_text(
        """
[[questions]]
id = "lots"
question = "Combien de lots ?"
pages = [3]
valeurs = ["59", "cinquante-neuf"]
""",
        encoding="utf-8",
    )

    corpus = charger_corpus(dossier)

    [tables] = corpus.controles_tables
    assert (tables.premiere_page, tables.derniere_page, tables.denominateur) == (1, 2, 2000)
    assert tables.tables[0].lots == 59
    assert corpus.controles_nombres[0].nombres == ("2.250.000", "32.700")
    assert corpus.questions[0].valeurs == ("59", "cinquante-neuf")


def test_un_controle_hors_du_corpus_est_refuse(tmp_path: Path) -> None:
    dossier = creer_corpus(tmp_path, pages=3)
    (dossier / "controles.toml").write_text(
        '[[nombres]]\nnom = "Prix"\npage = 9\nnombres = ["1"]\n', encoding="utf-8"
    )

    with pytest.raises(CorpusError, match="page 9"):
        charger_corpus(dossier)


def test_sans_fichier_de_controles_ni_de_questions_le_corpus_reste_utilisable(
    tmp_path: Path,
) -> None:
    corpus = charger_corpus(creer_corpus(tmp_path))

    assert corpus.controles_tables == ()
    assert corpus.questions == ()
