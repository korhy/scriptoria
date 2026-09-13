"""Logique de présentation de l'UI, sans Streamlit.

L'UI ne partage aucun code avec l'API ; ce module-ci n'importe ni Streamlit ni
httpx, ce qui permet de fixer par des tests ce que les pages affichent. Le rendu
lui-même est vérifié dans un navigateur.
"""

import pytest

from scriptoria.ui.presentation import (
    action_ocr,
    cle_naturelle,
    cle_widget,
    en_cours,
    index_selection,
    libelle_document,
    libelle_source,
    progression,
)


def document(status: str, *, page_count: int = 200, pages_transcribed: int = 0) -> dict:
    return {
        "id": "1a2b3c4d-0000-0000-0000-000000000000",
        "source_filename": "scan.png",
        "status": status,
        "page_count": page_count,
        "pages_transcribed": pages_transcribed,
        "created_at": "2026-09-13T10:34:12.123456Z",
    }


# --- Ordre d'import ---------------------------------------------------------


def test_les_pages_importees_suivent_l_ordre_naturel() -> None:
    """L'ordre d'import fixe les numéros de page : `page10` avant `page2` fausserait tout."""
    noms = ["page10.png", "Page2.png", "page1.png"]

    assert sorted(noms, key=cle_naturelle) == ["page1.png", "Page2.png", "page10.png"]


def test_l_ordre_naturel_tient_sans_chiffres_et_avec_separateurs() -> None:
    assert sorted(["b.png", "a.png"], key=cle_naturelle) == ["a.png", "b.png"]
    assert sorted(["scan-10.jpg", "scan-9.jpg"], key=cle_naturelle) == ["scan-9.jpg", "scan-10.jpg"]


# --- Progression ------------------------------------------------------------


def test_un_ocr_en_cours_montre_les_pages_faites() -> None:
    etat = progression(document("transcribing", pages_transcribed=3))

    assert etat.fraction == pytest.approx(3 / 200)
    assert etat.libelle == "OCR : 3 / 200 pages"


def test_un_ocr_en_echec_dit_ou_il_reprendra() -> None:
    """Relancer saute les pages faites : le dire évite de croire tout perdu."""
    etat = progression(document("failed", pages_transcribed=180))

    assert etat.fraction == pytest.approx(180 / 200)
    assert etat.libelle == "en échec — 180 / 200 pages déjà transcrites"


def test_un_echec_avant_l_ocr_ne_parle_pas_de_pages() -> None:
    etat = progression(document("failed"))

    assert etat.fraction == 0.0
    assert etat.libelle == "en échec"


@pytest.mark.parametrize("statut", ["awaiting_validation", "validated", "indexed"])
def test_apres_l_ocr_la_progression_est_complete(statut: str) -> None:
    """Un document saisi à la main n'a aucune page OCRisée, et il a pourtant franchi l'étape."""
    assert progression(document(statut, pages_transcribed=0)).fraction == 1.0


def test_avant_l_ocr_la_progression_est_nulle() -> None:
    etat = progression(document("preprocessed"))

    assert etat.fraction == 0.0
    assert etat.libelle == "prétraité"


def test_un_document_sans_page_ne_divise_pas_par_zero() -> None:
    assert progression(document("transcribing", page_count=0)).fraction == 0.0


def test_un_statut_inconnu_s_affiche_tel_quel() -> None:
    assert progression(document("archived")).libelle == "archived"


# --- Actions ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("statut", "attendu"),
    [
        ("new", True),
        ("preprocessing", True),
        ("transcribing", True),
        ("preprocessed", False),
        ("failed", False),
        ("indexed", False),
    ],
)
def test_seuls_les_documents_en_travail_se_rafraichissent(statut: str, attendu: bool) -> None:
    assert en_cours(document(statut)) is attendu


@pytest.mark.parametrize(
    ("statut", "attendu"),
    [
        ("preprocessed", "lancer"),
        # L'API décide si l'échec vient bien de l'OCR : l'UI propose, le 409 explique.
        ("failed", "relancer"),
        ("transcribing", None),
        ("awaiting_validation", None),
        ("indexed", None),
    ],
)
def test_l_action_ocr_depend_du_statut(statut: str, attendu: str | None) -> None:
    assert action_ocr(document(statut)) == attendu


# --- Libellés ---------------------------------------------------------------


def test_le_libelle_d_un_document_porte_sa_progression() -> None:
    libelle = libelle_document(document("transcribing", pages_transcribed=3))

    assert libelle == "scan.png — 200 p. — OCR : 3 / 200 pages — 2026-09-13 10:34 · 1a2b3c4d"


def test_deux_documents_de_meme_nom_ont_des_libelles_distincts() -> None:
    """Vu dans l'UI le 2026-09-13 : treize « correction.png — 1 p. — indexé » identiques.

    Impossible alors de savoir sur quel document on agit — et « Supprimer » agit.
    La date d'import et un identifiant court les départagent.
    """
    premier = document("indexed")
    second = {**document("indexed"), "id": "9f8e7d6c-0000-0000-0000-000000000000"}

    assert libelle_document(premier) != libelle_document(second)


def test_une_source_cite_le_fichier_et_la_page() -> None:
    """Une réponse invérifiable ne vaut rien : la source doit mener à la page."""
    hit = {"document_id": "1a2b3c4d-0000-0000-0000-000000000000", "page_number": 3}
    documents = {hit["document_id"]: document("indexed")}

    assert libelle_source(hit, documents) == "scan.png · page 3"


def test_une_source_d_un_document_inconnu_reste_identifiable() -> None:
    hit = {"document_id": "1a2b3c4d-0000-0000-0000-000000000000", "page_number": 3}

    assert libelle_source(hit, {}) == "document 1a2b3c4d · page 3"


# --- Sélection d'un document dans une liste qui change ----------------------


def test_la_selection_voulue_est_retrouvee_par_identifiant() -> None:
    assert index_selection(["a", "b", "c"], "b") == 1


@pytest.mark.parametrize("voulu", [None, "supprime"])
def test_une_selection_disparue_retombe_sur_le_premier_element(voulu: str | None) -> None:
    assert index_selection(["a", "b"], voulu) == 0


def test_la_cle_du_widget_est_stable_tant_que_la_liste_ne_change_pas() -> None:
    assert cle_widget("documents", ["a", "b"]) == cle_widget("documents", ["a", "b"])


@pytest.mark.parametrize("apres", [["a"], ["a", "b", "c"], ["b", "a"]])
def test_la_cle_du_widget_change_avec_la_liste(apres: list[str]) -> None:
    """Vu dans l'UI le 2026-09-13 : après une suppression, la liste gardait le libellé
    du document supprimé tandis que le panneau agissait sur un autre.

    Streamlit recalcule la valeur côté serveur mais pas le libellé affiché quand les
    options changent sous une même clé. Une clé neuve force un widget neuf.
    """
    assert cle_widget("documents", ["a", "b"]) != cle_widget("documents", apres)


def test_la_cle_du_widget_porte_son_prefixe() -> None:
    assert cle_widget("documents", ["a"]).startswith("documents-")
