"""Contrôles objectifs, sans texte de référence.

Les tables de tantièmes se somment à un total connu, mais elles sont **coupées
sur plusieurs pages** et deux tables de même dénominateur se suivent sur une même
page. Le contrôle suit donc les fractions dans l'ordre de lecture, et tranche une
table sur sa ligne de total.
"""

from scriptoria.evaluation.controles import (
    fractions,
    verifier_nombres_attendus,
    verifier_tables,
)
from scriptoria.evaluation.corpus import ControleNombres, ControleTables, TableAttendue


def test_les_fractions_sont_lues_dans_l_ordre_avec_leur_denominateur() -> None:
    texte = "Premier lot ci 70/2.000\nDeuxième lot ci 80/2.000°\nci 37/IOOO\nci 24/I.000"

    assert fractions(texte, 2000) == [70, 80]
    assert fractions(texte, 1000) == [37, 24]


def test_une_espace_dans_le_denominateur_est_toleree() -> None:
    assert fractions("ci 26/2 000", 2000) == [26]


def controle(pages: tuple[int, int], denominateur: int, *tables: TableAttendue) -> ControleTables:
    return ControleTables(
        nom="tantièmes",
        premiere_page=pages[0],
        derniere_page=pages[1],
        denominateur=denominateur,
        tables=tables,
    )


def test_une_table_coupee_sur_deux_pages_est_reconstituee() -> None:
    textes = {
        1: "lot 1 ci 600/1.000\nlot 2 ci",
        2: "300/1.000\nlot 3 ci 100/1.000\nTotal 1.000/1.000",
    }

    [resultat] = verifier_tables(textes, controle((1, 2), 1000, TableAttendue("A", 3, 1000)))

    assert (resultat.lots_lus, resultat.somme, resultat.total_lu) == (3, 1000, True)
    assert resultat.juste


def test_deux_tables_de_meme_denominateur_sont_separees_par_leur_total() -> None:
    textes = {1: "10/I.000 990/I.000 I.000/I.000 400/1.000", 2: "600/1.000 1.000/1.000"}

    valmy, coubertin = verifier_tables(
        textes,
        controle(
            (1, 2), 1000, TableAttendue("Valmy", 2, 1000), TableAttendue("Coubertin", 2, 1000)
        ),
    )

    assert valmy.juste and coubertin.juste
    assert coubertin.somme == 1000


def test_un_tantieme_mal_lu_fausse_la_somme() -> None:
    """C'est le cas à attraper : 26 lu 28 laisse une table plausible et fausse."""
    textes = {1: "970/2.000 28/2.000 2.000/2.000"}

    [resultat] = verifier_tables(textes, controle((1, 1), 2000, TableAttendue("G", 2, 2000)))

    assert resultat.somme == 998
    assert not resultat.juste


def test_une_ligne_de_total_illisible_est_signalee() -> None:
    textes = {1: "600/1.000 400/1.000 Total illisible"}

    [resultat] = verifier_tables(textes, controle((1, 1), 1000, TableAttendue("A", 2, 1000)))

    assert not resultat.total_lu
    assert not resultat.juste


def test_seules_les_pages_du_controle_sont_lues() -> None:
    textes = {1: "999/1.000", 2: "600/1.000 400/1.000 1.000/1.000", 3: "5/1.000"}

    [resultat] = verifier_tables(textes, controle((2, 2), 1000, TableAttendue("A", 2, 1000)))

    assert resultat.juste


def test_une_page_non_transcrite_est_signalee_et_non_supposee_vide() -> None:
    textes = {1: "600/1.000"}

    [resultat] = verifier_tables(textes, controle((1, 2), 1000, TableAttendue("A", 2, 1000)))

    assert resultat.pages_manquantes == (2,)
    assert not resultat.juste


def test_une_table_absente_est_rapportee_sans_lot() -> None:
    textes = {1: "600/1.000 400/1.000 1.000/1.000"}

    premiere, absente = verifier_tables(
        textes,
        controle((1, 1), 1000, TableAttendue("A", 2, 1000), TableAttendue("B", 3, 1000)),
    )

    assert premiere.juste
    assert (absente.lots_lus, absente.total_lu, absente.juste) == (0, False, False)


def test_les_nombres_attendus_sur_une_page() -> None:
    attendu = ControleNombres(nom="prix", page=6, nombres=("2.250.000", "2.217.300", "505.885"))

    resultat = verifier_nombres_attendus({6: "2.250.000 puis 2.2I7.300 et 505.855"}, attendu)

    assert resultat.trouves == ("2.250.000", "2.217.300")
    assert resultat.manquants == ("505.885",)
    assert not resultat.juste


def test_des_nombres_attendus_sur_une_page_non_transcrite_ne_sont_pas_trouves() -> None:
    attendu = ControleNombres(nom="prix", page=6, nombres=("2.250.000",))

    resultat = verifier_nombres_attendus({}, attendu)

    assert resultat.page_manquante
    assert not resultat.juste
