"""Lecture des tableaux Markdown produits par l'OCR.

Les offsets sont le point délicat : un bloc de confiance repère un fragment par
position dans le Markdown, si bien qu'une cellule mal située surlignerait le
mauvais passage dans l'UI de validation.

Le balisage à analyser vient d'un modèle, pas d'un générateur : il est parfois
irrégulier (ligne sans séparateur, cellules manquantes). Le parseur doit rendre
ce qu'il voit plutôt que d'exiger du Markdown canonique.
"""

from decimal import Decimal

from scriptoria.services.markdown_tables import parse_number, parse_tables

FACTURE = """FACTURE N° 2019-0447

| Désignation | Qté | PU HT | Total HT |
|-------------|-----|-------|----------|
| Papier A4 80g | 24 | 4,50 | 108,00 |
| Encre noire | 6 | 28,90 | 173,40 |

Total HT | 311,40 |
"""


# --- Nombres ----------------------------------------------------------------


def test_la_virgule_decimale_francaise_est_comprise() -> None:
    assert parse_number("28,90") == Decimal("28.90")


def test_les_separateurs_de_milliers_sont_ignores() -> None:
    """Espace insécable comprise : c'est ce que produit un modèle sur un montant."""
    assert parse_number("1 234,56") == Decimal("1234.56")
    assert parse_number("1\u00a0234,56") == Decimal("1234.56")


def test_le_symbole_monetaire_n_empeche_pas_la_lecture() -> None:
    assert parse_number("173,40 €") == Decimal("173.40")


def test_un_nombre_entier_est_lu() -> None:
    assert parse_number("24") == Decimal(24)


def test_un_texte_sans_nombre_ne_produit_rien() -> None:
    """Renvoyer 0 pour « Désignation » ferait échouer toute vérification en aval."""
    assert parse_number("Désignation") is None
    assert parse_number("") is None
    assert parse_number("-") is None


def test_un_libelle_accompagne_d_un_nombre_n_est_pas_un_nombre() -> None:
    """« 6 cartouches » est une quantité rédigée, pas une cellule numérique."""
    assert parse_number("6 cartouches") is None


# --- Tableaux ---------------------------------------------------------------


def test_un_tableau_complet_est_lu_avec_son_en_tete() -> None:
    tables = parse_tables(FACTURE)

    assert len(tables) == 2
    facture = tables[0]
    assert facture.header is not None
    assert facture.header.texts == ("Désignation", "Qté", "PU HT", "Total HT")
    assert len(facture.rows) == 2
    assert facture.rows[1].texts == ("Encre noire", "6", "28,90", "173,40")


def test_la_ligne_de_separation_n_est_pas_une_ligne_de_donnees() -> None:
    facture = parse_tables(FACTURE)[0]

    assert all("---" not in "".join(row.texts) for row in facture.rows)


def test_une_ligne_isolee_sans_en_tete_reste_lisible() -> None:
    """Le modèle rend parfois le total hors du tableau : ne pas le perdre."""
    total = parse_tables(FACTURE)[1]

    assert total.header is None
    assert total.rows[0].texts == ("Total HT", "311,40")


def test_les_offsets_d_une_cellule_pointent_sur_son_texte() -> None:
    """Invariant : le Markdown tranché aux offsets redonne exactement la cellule."""
    facture = parse_tables(FACTURE)[0]
    cellule = facture.rows[1].cells[2]

    assert FACTURE[cellule.start_offset : cellule.end_offset] == "28,90"


def test_les_offsets_d_une_ligne_couvrent_la_ligne_entiere() -> None:
    facture = parse_tables(FACTURE)[0]
    ligne = facture.rows[1]

    extrait = FACTURE[ligne.start_offset : ligne.end_offset]
    assert extrait == "| Encre noire | 6 | 28,90 | 173,40 |"


def test_un_markdown_sans_tableau_ne_produit_aucun_tableau() -> None:
    assert parse_tables("# Titre\n\nUn paragraphe sans pipe.") == []


def test_une_ligne_a_cellules_manquantes_est_rendue_telle_quelle() -> None:
    """Une cellule perdue par l'OCR est justement ce qu'on cherche à repérer."""
    markdown = "| A | B | C |\n|---|---|---|\n| 1 | | 3 |\n"

    ligne = parse_tables(markdown)[0].rows[0]

    assert ligne.texts == ("1", "", "3")


def test_les_tableaux_separes_par_du_texte_sont_distincts() -> None:
    markdown = "| A |\n|---|\n| 1 |\n\nUn paragraphe.\n\n| B |\n|---|\n| 2 |\n"

    tables = parse_tables(markdown)

    assert len(tables) == 2
    assert tables[0].rows[0].texts == ("1",)
    assert tables[1].rows[0].texts == ("2",)


def test_un_point_de_milliers_et_une_virgule_decimale_cohabitent() -> None:
    """`1.234,56` : le dernier séparateur est le décimal, l'autre marque les milliers."""
    assert parse_number("1.234,56") == Decimal("1234.56")


def test_des_points_de_milliers_sans_decimale_sont_compris() -> None:
    assert parse_number("1.234.567") == Decimal(1234567)
