"""Mesures d'exactitude d'une transcription.

Deux mesures, parce qu'elles ne disent pas la même chose :

- le **taux d'erreur par caractère** dit si le texte est globalement lisible ;
- l'**exactitude des nombres** dit si ce qui compte a été lu juste. Sur un acte
  notarié, `28,60` pour `28,90` coûte un seul caractère et fausse tout.
"""

import pytest

from scriptoria.evaluation.mesures import (
    distance_edition,
    exactitude_nombres,
    nombres,
    normaliser_mise_en_page,
    taux_erreur_caracteres,
)

# --- Distance et taux d'erreur ----------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "attendu"),
    [("", "", 0), ("abc", "abc", 0), ("abc", "", 3), ("chat", "chats", 1), ("28,90", "28,60", 1)],
)
def test_distance_edition(a: str, b: str, attendu: int) -> None:
    assert distance_edition(a, b) == attendu


def test_un_texte_identique_a_un_taux_nul() -> None:
    assert taux_erreur_caracteres("Total HT 311,40", "Total HT 311,40") == 0.0


def test_le_taux_rapporte_les_erreurs_a_la_longueur_de_la_reference() -> None:
    assert taux_erreur_caracteres("abcd", "abXd") == pytest.approx(0.25)


def test_une_reference_vide_est_refusee() -> None:
    """Un taux sur une référence vide n'aurait pas de sens : mieux vaut le dire."""
    with pytest.raises(ValueError, match="vide"):
        taux_erreur_caracteres("   ", "texte")


# --- Normalisation de mise en page -----------------------------------------


def test_les_tirets_de_remplissage_ne_comptent_pas() -> None:
    """Le notaire barrait les fins de ligne pour qu'on n'y ajoute rien : ce n'est pas du texte."""
    assert normaliser_mise_en_page("mil neuf--\ncent") == normaliser_mise_en_page("mil neuf cent")


def test_un_mot_coupe_en_fin_de_ligne_est_recolle() -> None:
    assert normaliser_mise_en_page("l'enregistre-\nment") == "l'enregistrement"


def test_les_espaces_et_retours_a_la_ligne_sont_uniformises() -> None:
    assert normaliser_mise_en_page("  Dépôt   du\n\n règlement ") == "Dépôt du règlement"


def test_la_mise_en_page_n_est_pas_comptee_comme_une_erreur() -> None:
    reference = "le vingt-sept avril mil neuf cent cinquante-trois"
    hypothese = "le vingt-sept avril mil neuf--\ncent cinquan-\nte-trois"

    assert taux_erreur_caracteres(reference, hypothese) == 0.0


# --- Nombres ----------------------------------------------------------------


def test_les_nombres_sont_extraits_dans_leur_forme_normalisee() -> None:
    texte = "prix de 2.250.000 francs, lot pour 70/2.000, volume I70 c, 28,90 €"

    assert sorted(nombres(texte)) == ["170", "2250000", "28,90", "70/2000"]


def test_le_i_de_la_machine_a_ecrire_vaut_un() -> None:
    """Les machines de l'époque n'avaient pas toujours de touche 1 : on tapait un I."""
    assert nombres("2.2I7.300") == nombres("2.217.300")


def test_les_chiffres_romains_et_le_pronom_ne_sont_pas_des_nombres() -> None:
    assert nombres("TITRE II - I - Du chef de ladite Société") == {}


def test_une_espace_de_milliers_ne_coupe_pas_un_nombre() -> None:
    assert nombres("2 250 000 francs") == nombres("2.250.000 francs")


def test_deux_nombres_voisins_ne_sont_pas_fusionnes() -> None:
    assert sorted(nombres("lots 6 et 28")) == ["28", "6"]


def test_exactitude_des_nombres() -> None:
    reference = "6 cartouches à 28,90 soit 173,40"
    hypothese = "6 cartouches à 28,60 soit 173,40"

    score = exactitude_nombres(reference, hypothese)

    assert (score.attendus, score.lus, score.justes) == (3, 3, 2)
    assert score.rappel == pytest.approx(2 / 3)
    assert score.precision == pytest.approx(2 / 3)


def test_sans_nombre_attendu_le_rappel_est_absent_pas_parfait() -> None:
    """Un texte sans nombre n'a rien à rater : le dire 1,0 serait une fausse bonne nouvelle."""
    score = exactitude_nombres("aucun chiffre", "aucun chiffre")

    assert score.rappel is None
    assert score.precision is None
