"""Galerie de l'écran de validation : ce que dit chaque vignette, où mène chaque clic.

Tout ce qui se décide ici se décide sans Streamlit : les pages de l'UI sont
exclues de la couverture et vérifiées dans un navigateur. Ce qui peut tromper le
relecteur — une alerte masquée, un clic qui mène à la mauvaise page, une
validation groupée qui tait les pages douteuses — se fixe dans ces tests.
"""

import pytest

from scriptoria.ui.presentation import (
    SEUIL_ALERTE,
    avancement,
    confirmation_validation_groupee,
    decouper,
    filtrer_pages,
    index_paquet,
    libelle_avancement,
    libelle_paquet,
    libelle_vignette,
    page_voisine,
    prochaine_a_relire,
    refus_validation_groupee,
    revisions_affichees,
    texte_modifie,
    vignette,
)


def page(
    numero: int,
    state: str = "to_review",
    score: float = 1.0,
    *,
    en_lot: bool = False,
    revision: int = 1,
) -> dict:
    """Une page telle que la rend `GET /documents/{id}/pages`."""
    transcrite = state != "untranscribed"
    return {
        "id": f"p{numero}",
        "page_number": numero,
        "state": state,
        "confidence_score": score if transcrite else None,
        "latest_revision": revision if transcrite else None,
        "bulk_validated": en_lot,
    }


def document(status: str = "awaiting_validation") -> dict:
    return {"id": "d1", "status": status}


# --- Vignette ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "en_lot", "attendu"),
    [
        ("untranscribed", False, "non transcrite"),
        ("to_review", False, "à relire"),
        ("draft", False, "brouillon"),
        ("validated", False, "validée"),
        ("validated", True, "validée en lot"),
    ],
)
def test_la_vignette_dit_l_etat_de_la_page(state: str, en_lot: bool, attendu: str) -> None:
    assert vignette(page(1, state, en_lot=en_lot)).libelle == attendu


def test_le_score_s_affiche_a_la_francaise() -> None:
    assert vignette(page(1, score=0.15)).score == "0,15"


def test_une_page_non_transcrite_n_affiche_aucun_score() -> None:
    """Pas de transcription, pas de score — surtout pas un 1,00 rassurant."""
    assert vignette(page(1, "untranscribed")).score == "—"


def test_une_page_sous_le_seuil_porte_une_alerte() -> None:
    douteuse = vignette(page(1, score=0.15))

    assert douteuse.alerte is True
    assert douteuse.icone == "⚠️"


def test_le_seuil_lui_meme_ne_declenche_pas_d_alerte() -> None:
    """« En dessous du seuil », comme le second passage de l'OCR."""
    assert vignette(page(1, score=SEUIL_ALERTE)).alerte is False


def test_une_page_validee_en_lot_garde_son_alerte() -> None:
    """Validée sans être lue : c'est précisément la page qu'il faudra rouvrir."""
    assert vignette(page(1, "validated", 0.15, en_lot=True)).alerte is True


def test_le_libelle_de_la_vignette_porte_le_numero_et_le_score() -> None:
    libelle = libelle_vignette(page(6, score=0.15))

    assert "p. 6" in libelle
    assert "0,15" in libelle
    assert libelle.startswith("⚠️")


def test_chaque_etat_a_son_icone() -> None:
    etats = [
        page(1, "untranscribed"),
        page(2, "to_review"),
        page(3, "draft"),
        page(4, "validated"),
        page(5, "validated", en_lot=True),
    ]

    icones = [vignette(p).icone for p in etats]

    assert len(set(icones)) == len(icones)


# --- Filtres ---------------------------------------------------------------------


@pytest.fixture
def pages() -> list[dict]:
    return [
        page(1, "validated"),
        page(2, "to_review", 0.15),
        page(3, "draft"),
        page(4, "untranscribed"),
        page(5, "validated", 0.2, en_lot=True),
    ]


@pytest.mark.parametrize(
    ("filtre", "numeros"),
    [
        ("toutes", [1, 2, 3, 4, 5]),
        ("à relire", [2, 3, 4]),
        ("alertes", [2, 5]),
        ("validées", [1, 5]),
    ],
)
def test_les_filtres_de_la_galerie(pages: list[dict], filtre: str, numeros: list[int]) -> None:
    assert [p["page_number"] for p in filtrer_pages(pages, filtre)] == numeros


def test_un_filtre_inconnu_est_une_erreur(pages: list[dict]) -> None:
    """Retomber sur « toutes » masquerait une faute de frappe dans l'UI."""
    with pytest.raises(ValueError, match="filtre"):
        filtrer_pages(pages, "douteuses")


# --- Navigation ------------------------------------------------------------------


def test_la_page_suivante_et_la_precedente(pages: list[dict]) -> None:
    assert page_voisine(pages, "p2", +1) == "p3"
    assert page_voisine(pages, "p2", -1) == "p1"


def test_aux_bornes_il_n_y_a_pas_de_voisine(pages: list[dict]) -> None:
    assert page_voisine(pages, "p1", -1) is None
    assert page_voisine(pages, "p5", +1) is None


def test_une_page_absente_de_la_liste_n_a_pas_de_voisine(pages: list[dict]) -> None:
    assert page_voisine(pages, "p99", +1) is None


def test_la_prochaine_a_relire_suit_la_page_courante(pages: list[dict]) -> None:
    assert prochaine_a_relire(pages, "p2") == "p3"


def test_la_prochaine_a_relire_saute_les_pages_validees_et_reprend_au_debut() -> None:
    liste = [page(1), page(2, "validated"), page(3, "validated")]

    assert prochaine_a_relire(liste, "p1") is None
    assert prochaine_a_relire([*liste, page(4)], "p4") == "p1"


def test_une_page_validee_en_lot_n_est_plus_a_relire(pages: list[dict]) -> None:
    """Elle reste repérable par son alerte ; « à relire » suit l'état, pas le score."""
    assert prochaine_a_relire(pages, "p4") == "p2"


def test_depuis_une_page_inconnue_la_prochaine_est_la_premiere_a_relire(
    pages: list[dict],
) -> None:
    assert prochaine_a_relire(pages, None) == "p2"


# --- Avancement et validation groupée ------------------------------------------


def test_l_avancement_compte_les_pages_validees_et_nomme_les_alertes(pages: list[dict]) -> None:
    etat = avancement(pages)

    assert (etat.validees, etat.total, etat.alertes) == (2, 5, (2, 5))
    assert libelle_avancement(etat) == "2 / 5 pages validées · 2 alertes (p. 2, 5)"


def test_sans_alerte_l_avancement_n_en_parle_pas() -> None:
    assert libelle_avancement(avancement([page(1, "validated")])) == "1 / 1 page validée"


def test_la_validation_groupee_est_possible_sur_un_document_a_valider() -> None:
    assert refus_validation_groupee(document(), [page(1), page(2, "draft")]) is None


@pytest.mark.parametrize("statut", ["transcribing", "preprocessed", "failed"])
def test_la_validation_groupee_attend_la_fin_de_la_transcription(statut: str) -> None:
    raison = refus_validation_groupee(document(statut), [page(1)])

    assert raison is not None
    assert "transcri" in raison


def test_la_validation_groupee_nomme_les_pages_non_transcrites() -> None:
    raison = refus_validation_groupee(document(), [page(1), page(3, "untranscribed")])

    assert raison is not None
    assert "p. 3" in raison


def test_rien_a_valider_quand_tout_l_est_deja() -> None:
    raison = refus_validation_groupee(document("indexed"), [page(1, "validated")])

    assert raison == "Toutes les pages sont déjà validées."


def test_la_confirmation_dit_combien_de_pages_et_lesquelles_portent_une_alerte(
    pages: list[dict],
) -> None:
    restantes = [p for p in pages if p["state"] != "untranscribed"]

    texte = confirmation_validation_groupee(restantes)

    assert "2 pages" in texte
    assert "p. 2" in texte
    assert "référence" in texte


def test_la_confirmation_sans_alerte_ne_cite_aucune_page() -> None:
    texte = confirmation_validation_groupee([page(1), page(2)])

    assert "alerte" not in texte
    assert "2 pages" in texte


def test_les_revisions_affichees_sont_celles_de_la_galerie() -> None:
    """Ce que la validation groupée envoie : ce que le relecteur avait sous les yeux."""
    assert revisions_affichees([page(1, revision=3), page(2, "untranscribed")]) == {
        "p1": 3,
        "p2": 0,
    }


# --- Édition et paquets ------------------------------------------------------------


def test_un_texte_retouche_seulement_en_bordure_n_est_pas_modifie() -> None:
    assert texte_modifie("texte\n", "  texte") is False
    assert texte_modifie("texte corrigé", "texte") is True


def test_la_galerie_se_decoupe_en_paquets() -> None:
    liste = [page(numero) for numero in range(1, 6)]

    assert [[p["page_number"] for p in paquet] for paquet in decouper(liste, 2)] == [
        [1, 2],
        [3, 4],
        [5],
    ]


def test_le_paquet_affiche_est_celui_de_la_page_ouverte() -> None:
    paquets = decouper([page(numero) for numero in range(1, 6)], 2)

    assert index_paquet(paquets, "p3") == 1
    assert index_paquet(paquets, "p99") == 0


def test_un_paquet_se_nomme_par_ses_pages_extremes() -> None:
    paquets = decouper([page(numero) for numero in range(1, 6)], 2)

    assert [libelle_paquet(paquet) for paquet in paquets] == ["p. 1-2", "p. 3-4", "p. 5-5"]
