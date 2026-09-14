"""Mise en forme d'une transcription dactylographiée.

Relevé sur un règlement de copropriété de 1953 (44 pages, 2026-09-14) : le modèle
recopie la mise en ligne de la machine à écrire. Le piège est dans les tirets de
fin de ligne — **la plupart ne coupent pas un mot** : le notaire remplit la fin de
ligne de tirets pour qu'on n'y ajoute rien. Sur 272 fins de ligne à tiret :

- ~170 tirets de remplissage (`contrat-⏎ne contenait`, `l'épouse-⏎Qu'ils`) ;
- ~56 vraies césures (`de-⏎niers`, `stipu-⏎lés`) ;
- ~17 nombres composés (`vingt-⏎sept`), dont le tiret reste.

Recoller à l'aveugle donnerait `contratne`. D'où un lexique tiré des textes
eux-mêmes, et une règle : **dans le doute, ne rien toucher et le signaler.**

Les exemples sont synthétiques : le corpus réel porte des noms et reste hors Git.
"""

import pytest

from scriptoria.services.layout import (
    LayoutError,
    PageText,
    build_lexicon,
    normalize_pages,
    verify_content_preserved,
)

# Mots « connus » : ce qu'on trouverait écrit en entier ailleurs dans le document.
VOCABULAIRE = (
    "contrat ne contenait prix principal existence stipulés civil deniers "
    "épouse la société vis-à-vis sus-énoncée ma"
)


def mettre_en_forme(texte: str, vocabulaire: str = VOCABULAIRE) -> str:
    (page,) = normalize_pages([PageText(1, texte)], known_texts=[vocabulaire])
    return page.text


# --- Lexique ----------------------------------------------------------------


def test_le_lexique_ignore_les_fragments_de_mots_coupes() -> None:
    """« niers » n'est pas un mot : l'admettre ferait prendre la césure pour du remplissage."""
    lexique = build_lexicon(["des de-\nniers publics"])

    assert "publics" in lexique
    assert "niers" not in lexique


def test_le_lexique_retient_les_mots_composes() -> None:
    assert "vis-à-vis" in build_lexicon(["notamment vis-à-vis des tiers"])


# --- Tirets de fin de ligne -------------------------------------------------


def test_une_cesure_est_recollee_quand_le_mot_entier_est_connu() -> None:
    assert mettre_en_forme("l'exis-\ntence de deux") == "l'existence de deux"


def test_un_tiret_de_remplissage_entre_deux_mots_connus_devient_une_espace() -> None:
    assert (
        mettre_en_forme("lequel contrat-\nne contenait rien") == "lequel contrat ne contenait rien"
    )


def test_un_tiret_de_remplissage_avant_une_majuscule_clot_le_paragraphe() -> None:
    """Le notaire a rempli la ligne jusqu'au bout : la phrase suivante est un autre alinéa."""
    assert mettre_en_forme("civile de l'épouse-\nQu'ils n'étaient pas") == (
        "civile de l'épouse\n\nQu'ils n'étaient pas"
    )


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("le vingt--\ntrois avril", "le vingt-trois avril"),
        ("cinquante-\nquatre francs", "cinquante-quatre francs"),
        ("le vingt-\nseptième lot", "le vingt-septième lot"),
        ("soixante-\ndix", "soixante-dix"),
    ],
)
def test_un_nombre_compose_garde_son_tiret(texte: str, attendu: str) -> None:
    assert mettre_en_forme(texte) == attendu


def test_un_mot_compose_connu_garde_son_tiret() -> None:
    assert mettre_en_forme("l'inscription sus-\nénoncée") == "l'inscription sus-énoncée"


def test_deux_moities_inconnues_sont_recollees() -> None:
    """Ni « réali » ni « sée » ne sont des mots : ce ne peut être qu'une césure."""
    assert mettre_en_forme("fut réali-\nsée hier") == "fut réalisée hier"


def test_un_cas_indecis_est_laisse_tel_quel_et_signale() -> None:
    """« ma » est un mot, « risés » non, « marisés » non plus : on ne devine pas."""
    (page,) = normalize_pages([PageText(1, "gens ma-\nrisés ici")], known_texts=[VOCABULAIRE])

    assert "ma-\nrisés" in page.text
    assert [page.text[debut:fin] for debut, fin in page.uncertain] == ["ma-\nrisés"]


def test_un_remplissage_precede_d_une_espace_disparait() -> None:
    assert mettre_en_forme("un appartement --\nde deux pièces") == "un appartement de deux pièces"


def test_des_tirets_de_remplissage_dans_la_ligne_disparaissent() -> None:
    assert mettre_en_forme("mil neuf-- cent") == "mil neuf cent"


def test_une_ligne_sans_lettre_n_est_pas_un_tiret_de_remplissage() -> None:
    assert mettre_en_forme("Titre\n\n---\n\nsuite") == "Titre\n\n---\n\nsuite"


# --- Ponctuation ------------------------------------------------------------


def test_une_virgule_suivie_d_une_lettre_recoit_son_espace() -> None:
    assert mettre_en_forme("vingt-quatre,lequel") == "vingt-quatre, lequel"


def test_une_virgule_decimale_reste_collee() -> None:
    assert mettre_en_forme("soit 28,90 francs") == "soit 28,90 francs"


# --- Lignes et paragraphes --------------------------------------------------


def test_les_lignes_d_une_meme_phrase_sont_recollees() -> None:
    assert mettre_en_forme("n'avaient jamais été\ntuteurs de mineurs") == (
        "n'avaient jamais été tuteurs de mineurs"
    )


def test_une_phrase_finie_suivie_d_une_majuscule_ouvre_un_paragraphe() -> None:
    assert mettre_en_forme("en premières noces.\nLes vendeurs ont") == (
        "en premières noces.\n\nLes vendeurs ont"
    )


def test_un_nom_propre_en_debut_de_ligne_continue_la_phrase() -> None:
    assert mettre_en_forme("passé devant Me\nDURAND, notaire") == "passé devant Me DURAND, notaire"


def test_un_tableau_garde_une_ligne_par_rangee() -> None:
    tableau = "| Premier lot,ci | 26/2.000 |\n| Deuxième lot,ci | 40/2.000 |"

    assert mettre_en_forme(tableau) == (
        "| Premier lot, ci | 26/2.000 |\n| Deuxième lot, ci | 40/2.000 |"
    )


def test_un_element_de_liste_commence_une_ligne_et_sa_suite_s_y_recolle() -> None:
    texte = "déclaré ce qui suit:\n- Qu'ils étaient mariés\nsous le régime légal\n2°- Sur les lots"

    assert mettre_en_forme(texte) == (
        "déclaré ce qui suit:\n- Qu'ils étaient mariés sous le régime légal\n2°- Sur les lots"
    )


def test_un_titre_en_capitales_reste_seul_sur_sa_ligne() -> None:
    assert mettre_en_forme("DÉSIGNATION\nL'immeuble comprend") == (
        "DÉSIGNATION\n\nL'immeuble comprend"
    )


def test_les_espaces_superflues_disparaissent() -> None:
    assert mettre_en_forme("\n\n  un   texte  \n\n\n\nla suite  \n") == "un texte\n\nla suite"


def test_un_tiret_avant_une_enumeration_clot_la_ligne() -> None:
    """Relevé p. 37 : `papeterie-⏎f)- la consommation`. « f » n'est pas la fin d'un mot."""
    assert mettre_en_forme("frais de poste et papeterie-\nf)- la consommation d'eau") == (
        "frais de poste et papeterie\nf)- la consommation d'eau"
    )


def test_une_ligne_qui_finit_par_un_nombre_ne_se_recolle_pas_a_la_suivante() -> None:
    """Relevé p. 30 : une liste de lots, un lot par ligne, sans ponctuation finale."""
    texte = "Dixième lot, pour vingt-six/millièmes, ci 26/1.000°\nOnzième lot, ci 25/I.000\nDouze"

    assert mettre_en_forme(texte) == texte


# --- Coupures déjà remises en ligne par le modèle ---------------------------


def test_une_cesure_remise_en_ligne_par_le_modele_est_recollee() -> None:
    """Relevé p. 20 : le modèle a écrit `com - prenant` au lieu de `com-⏎prenant`."""
    assert mettre_en_forme("un appartement com - prenant: entrée") == (
        "un appartement comprenant: entrée"
    )


def test_un_remplissage_remis_en_ligne_par_le_modele_devient_une_espace() -> None:
    vocabulaire = f"{VOCABULAIRE} appartement comprenant"

    assert mettre_en_forme("un appartement - comprenant: entrée", vocabulaire) == (
        "un appartement comprenant: entrée"
    )
    assert mettre_en_forme("un appartement- comprenant: entrée", vocabulaire) == (
        "un appartement comprenant: entrée"
    )


def test_un_nombre_compose_remis_en_ligne_par_le_modele_retrouve_son_tiret() -> None:
    assert mettre_en_forme("le vingt- six avril") == "le vingt-six avril"


def test_un_nom_en_capitales_suivi_d_un_tiret_n_est_ni_touche_ni_signale() -> None:
    """Relevé p. 5, 11, 15, 17 : `Me DURAND - notaire`. Une incise, pas un mot coupé."""
    (page,) = normalize_pages([PageText(1, "Et devant Me DURAND - notaire à Paris")])

    assert page.text == "Et devant Me DURAND - notaire à Paris"
    assert page.uncertain == ()


def test_un_tiret_d_incise_avant_une_majuscule_est_garde() -> None:
    assert mettre_en_forme("Treizième lot - AU TROISIÈME ETAGE") == (
        "Treizième lot - AU TROISIÈME ETAGE"
    )


# --- Numéros de page --------------------------------------------------------


def test_les_numeros_de_page_sont_retires_quelle_que_soit_leur_forme() -> None:
    """Formes relevées : `-3-`, `-IO-` (I et O tapés pour 1 et 0), `- II-`, `14-`, `12`."""
    pages = [
        PageText(11, "-IO-\ntexte onze"),
        PageText(12, "- II-\ntexte douze"),
        PageText(13, "12\ntexte treize"),
        PageText(15, "texte quinze\n14-"),
    ]

    assert [page.text for page in normalize_pages(pages)] == [
        "texte onze",
        "texte douze",
        "texte treize",
        "texte quinze",
    ]


def test_un_nombre_isole_qui_ne_suit_pas_la_numerotation_est_garde() -> None:
    pages = [PageText(4, "-3-\nun"), PageText(5, "-4-\ndeux"), PageText(6, "1953\ntrois")]

    assert normalize_pages(pages)[2].text == "1953\ntrois"


def test_sans_numerotation_confirmee_aucun_nombre_n_est_retire() -> None:
    """Une seule page numérotée ne prouve rien : ce peut être un article."""
    assert normalize_pages([PageText(4, "-3-\ntexte")])[0].text == "-3-\ntexte"


def test_un_tiret_de_liste_n_est_pas_un_numero_de_page() -> None:
    pages = [PageText(4, "-3-\nun"), PageText(5, "-4-\ndeux"), PageText(6, "- L'immeuble")]

    assert normalize_pages(pages)[2].text == "- L'immeuble"


def test_une_cloture_de_code_orpheline_est_retiree() -> None:
    """Page 13 du règlement : ```markdown en tête, jamais refermé."""
    pages = [PageText(12, "- II-\nun"), PageText(13, "```markdown\n12\njour suivant")]

    assert normalize_pages(pages)[1].text == "jour suivant"


# --- Mot coupé entre deux pages ---------------------------------------------


def test_un_mot_coupe_en_fin_de_page_est_recolle_sur_la_page_ou_il_commence() -> None:
    pages = [
        PageText(10, "-9-\nl'exis-"),
        PageText(11, "-IO-\ntence de deux inscriptions"),
    ]

    fin, debut = normalize_pages(pages, known_texts=[VOCABULAIRE])

    assert fin.text == "l'existence"
    assert debut.text == "de deux inscriptions"


def test_un_nombre_compose_coupe_entre_deux_pages_garde_son_tiret() -> None:
    fin, debut = normalize_pages([PageText(9, "le vingt--"), PageText(10, "trois avril")])

    assert (fin.text, debut.text) == ("le vingt-trois", "avril")


def test_la_ponctuation_accrochee_au_fragment_le_suit() -> None:
    fin, debut = normalize_pages([PageText(1, "à Pa-"), PageText(2, "ris,le vingt")])

    assert (fin.text, debut.text) == ("à Paris,", "le vingt")


def test_un_remplissage_en_fin_de_page_ne_deplace_rien() -> None:
    fin, debut = normalize_pages(
        [PageText(1, "le prix-"), PageText(2, "principal est payé")], known_texts=[VOCABULAIRE]
    )

    assert (fin.text, debut.text) == ("le prix", "principal est payé")


def test_une_page_relue_n_est_ni_modifiee_ni_rendue() -> None:
    """Une révision humaine ne se réécrit pas : la voisine garde sa coupure."""
    pages = [PageText(1, "l'exis-"), PageText(2, "tence", editable=False)]

    (fin,) = normalize_pages(pages, known_texts=[VOCABULAIRE])

    assert fin.page_number == 1
    assert fin.text == "l'exis-"


# --- Garde-fou --------------------------------------------------------------


def test_la_mise_en_forme_ne_change_aucune_lettre_ni_aucun_chiffre() -> None:
    texte = (
        "-4-\nleur affaire,sans recours contre la société-\nQu'ils déclarent le prix-\n"
        "principal de 2.250.000 francs,soit vingt--\ntrois lots.\n| lot,ci | 26/2.000 |"
    )
    pages = [PageText(4, "-3-\nun"), PageText(5, texte)]

    resultat = normalize_pages(pages, known_texts=[VOCABULAIRE])[1].text

    assert "2.250.000" in resultat
    lettres = [c for c in resultat if c.isalnum()]
    assert lettres == [c for c in texte.removeprefix("-4-") if c.isalnum()]


def test_le_garde_fou_refuse_un_contenu_modifie() -> None:
    with pytest.raises(LayoutError, match="page 2"):
        verify_content_preserved(
            before=[(1, "prix 28,90"), (2, "total 173,40")],
            after=[(1, "prix 28,90"), (2, "total 173,4O")],
        )


def test_le_garde_fou_admet_un_fragment_deplace_d_une_page_a_l_autre() -> None:
    verify_content_preserved(
        before=[(1, "l'exis-"), (2, "tence de")],
        after=[(1, "l'existence"), (2, "de")],
    )
