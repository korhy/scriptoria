"""Indice de confiance sur une transcription.

Le mode de défaillance à redouter sur ce projet n'est pas la sortie cassée —
elle se repère — mais le **chiffre plausible et faux**, qui se recopie dans
l'archive. La mesure du 2026-09-11 en donne le cas d'école : `28,60` lu au lieu
de `28,90`, total de ligne `173,40` intact, ligne arithmétiquement fausse sans
que rien ne paraisse anormal à la lecture.

C'est ce cas précis que ces tests exigent d'attraper, et c'est pourquoi la
cohérence arithmétique compte ici plus que le score déclaratif du modèle : elle
est vérifiable.
"""

from scriptoria.services.confidence import (
    METHOD_ARITHMETIC,
    METHOD_DOUBLE_PASS,
    METHOD_STRUCTURAL,
    ConfidenceBlock,
    aggregate_page_score,
    analyse_markdown,
    compare_passes,
)

# Le cas mesuré : seule la ligne « Encre » est incohérente (6 x 28,60 ≠ 173,40).
FACTURE_CORROMPUE = """FACTURE N° 2019-0447

| Désignation | Qté | PU HT | Total HT |
|-------------|-----|-------|----------|
| Papier A4 80g | 24 | 4,50 | 108,00 |
| Encre noire | 6 | 28,60 | 173,40 |
| Reliure spirale | 2 | 15,00 | 30,00 |

Total HT | 311,40 |
TVA 20% | 62,28 |
Total TTC | 373,68 |
"""

FACTURE_SAINE = FACTURE_CORROMPUE.replace("28,60", "28,90")


def methodes(blocks: list[ConfidenceBlock]) -> set[str]:
    return {block.method for block in blocks}


def extraits(blocks: list[ConfidenceBlock], markdown: str) -> list[str]:
    return [markdown[block.start_offset : block.end_offset] for block in blocks]


# --- Cohérence arithmétique -------------------------------------------------


def test_une_ligne_arithmetiquement_fausse_est_signalee() -> None:
    """Le cas mesuré : 6 x 28,60 = 171,60, mais la ligne affiche 173,40."""
    blocks = analyse_markdown(FACTURE_CORROMPUE)

    arithmetiques = [block for block in blocks if block.method == METHOD_ARITHMETIC]
    assert len(arithmetiques) == 1
    assert "28,60" in extraits(arithmetiques, FACTURE_CORROMPUE)[0]


def test_une_facture_coherente_ne_declenche_aucune_alerte_arithmetique() -> None:
    blocks = analyse_markdown(FACTURE_SAINE)

    assert METHOD_ARITHMETIC not in methodes(blocks)


def test_un_total_qui_ne_somme_pas_les_lignes_est_signale() -> None:
    """Si le total lui-même est corrompu, le produit des lignes reste juste."""
    fausse_somme = FACTURE_SAINE.replace("Total HT | 311,40 |", "Total HT | 314,40 |")

    blocks = [
        block for block in analyse_markdown(fausse_somme) if block.method == METHOD_ARITHMETIC
    ]

    assert blocks, "aucune somme de lignes ne correspond au total annoncé"


def test_un_total_ttc_different_du_total_ht_n_est_pas_une_erreur() -> None:
    """Un document porte plusieurs totaux : n'en exiger qu'un serait absurde."""
    blocks = analyse_markdown(FACTURE_SAINE)

    assert blocks == [] or METHOD_ARITHMETIC not in methodes(blocks)


def test_un_arrondi_au_centime_est_tolere() -> None:
    """3 x 9,99 = 29,97 : une facture arrondie à 29,98 n'est pas une erreur d'OCR."""
    markdown = "| Article | Qté | PU | Total |\n|---|---|---|---|\n| Vis | 3 | 9,99 | 29,98 |\n"

    blocks = analyse_markdown(markdown)

    assert METHOD_ARITHMETIC not in methodes(blocks)


def test_un_tableau_sans_chiffre_ne_produit_aucune_verification() -> None:
    markdown = "| Nom | Ville |\n|---|---|\n| Durand | Lyon |\n"

    assert analyse_markdown(markdown) == []


def test_un_texte_libre_ne_produit_aucune_verification() -> None:
    assert analyse_markdown("# Lettre\n\nMonsieur,\n\nVeuillez agréer...\n") == []


# --- Signaux structurels ----------------------------------------------------


def test_un_passage_declare_illisible_par_le_modele_est_signale() -> None:
    """Le modèle dit lui-même qu'il n'a pas lu : le signal le plus fiable qu'il donne."""
    markdown = "Montant : [illisible] euros\n"

    blocks = analyse_markdown(markdown)

    assert methodes(blocks) == {METHOD_STRUCTURAL}
    assert extraits(blocks, markdown) == ["[illisible]"]


def test_une_cellule_vide_dans_un_tableau_est_signalee() -> None:
    markdown = "| Article | Qté | PU |\n|---|---|---|\n| Vis | | 9,99 |\n"

    blocks = analyse_markdown(markdown)

    assert METHOD_STRUCTURAL in methodes(blocks)


def test_une_ligne_plus_courte_que_l_en_tete_est_signalee() -> None:
    """Une colonne perdue décale toutes les valeurs : corruption silencieuse typique."""
    markdown = "| Article | Qté | PU | Total |\n|---|---|---|---|\n| Vis | 3 | 29,97 |\n"

    blocks = analyse_markdown(markdown)

    assert METHOD_STRUCTURAL in methodes(blocks)


def test_un_caractere_de_remplacement_est_signale() -> None:
    markdown = "Facture n� 2019\n"

    assert METHOD_STRUCTURAL in methodes(analyse_markdown(markdown))


def test_chaque_bloc_pointe_sur_un_fragment_reellement_present() -> None:
    """Invariant : sans offsets justes, l'UI surligne le mauvais passage."""
    blocks = analyse_markdown(FACTURE_CORROMPUE)

    for block in blocks:
        assert 0 <= block.start_offset < block.end_offset <= len(FACTURE_CORROMPUE)
        assert FACTURE_CORROMPUE[block.start_offset : block.end_offset].strip()


def test_tout_score_reste_dans_zero_un() -> None:
    for block in analyse_markdown(FACTURE_CORROMPUE):
        assert 0.0 <= block.score <= 1.0


# --- Agrégation -------------------------------------------------------------


def test_une_page_sans_alerte_vaut_un() -> None:
    """L'absence de signal, pas une preuve d'exactitude — la validation reste humaine."""
    assert aggregate_page_score([]) == 1.0


def test_un_seul_bloc_tres_incertain_fait_chuter_la_page() -> None:
    """Une moyenne laisserait passer une page partiellement fausse : c'est le piège."""
    blocks = [
        ConfidenceBlock(start_offset=0, end_offset=5, score=1.0, method="x"),
        ConfidenceBlock(start_offset=6, end_offset=9, score=1.0, method="x"),
        ConfidenceBlock(start_offset=10, end_offset=20, score=0.1, method="x"),
    ]

    score = aggregate_page_score(blocks)

    assert score == 0.1
    moyenne = sum(block.score for block in blocks) / len(blocks)
    assert score < moyenne


def test_le_score_de_page_reste_dans_zero_un() -> None:
    blocks = [ConfidenceBlock(start_offset=0, end_offset=1, score=0.42, method="x")]

    assert 0.0 <= aggregate_page_score(blocks) <= 1.0


# --- Double passage ---------------------------------------------------------


def test_deux_passages_identiques_ne_signalent_rien() -> None:
    """Une transcription stable est le cas nominal : aucun bloc à produire."""
    texte = "| Vis | 3 | 9,99 | 29,97 |\n"

    assert compare_passes(texte, texte) == []


def test_une_divergence_entre_passages_est_signalee_sur_le_premier() -> None:
    """Les offsets portent sur la révision conservée, la seule qu'on surlignera."""
    premier = "Montant : 28,90 euros\nRéférence : A-447\n"
    second = "Montant : 28,60 euros\nRéférence : A-447\n"

    blocks = compare_passes(premier, second)

    assert methodes(blocks) == {METHOD_DOUBLE_PASS}
    assert "28,90" in extraits(blocks, premier)[0]


def test_une_divergence_totale_donne_un_score_plancher() -> None:
    blocks = compare_passes("abcdef\n", "zzzzzz\n")

    assert blocks[0].score < 0.2


def test_une_ligne_absente_du_second_passage_est_signalee() -> None:
    premier = "ligne un\nligne deux\n"
    second = "ligne un\n"

    blocks = compare_passes(premier, second)

    assert "ligne deux" in extraits(blocks, premier)[0]


def test_une_difference_de_mise_en_forme_ne_compte_pas_comme_divergence() -> None:
    """Observé sur deux passages réels : le modèle espace différemment la même ligne.

    Une divergence d'espacement n'est pas une divergence de transcription. La
    signaler remplirait la base de blocs sans contenu et ferait surligner des
    lignes justes dans l'UI de validation.
    """
    premier = "Échéance : 30 jours\n|  Total HT  |  311,40  |\n"
    second = "Échéance: 30 jours\n| Total HT | 311,40 |\n"

    assert compare_passes(premier, second) == []


def test_une_divergence_de_chiffres_reste_signalee_malgre_l_espacement() -> None:
    premier = "| Total HT  | 311,40 |\n"
    second = "| Total HT | 314,40 |\n"

    blocks = compare_passes(premier, second)

    assert len(blocks) == 1
    assert blocks[0].score <= 0.2


def test_un_tableau_sans_en_tete_ne_declenche_aucune_multiplication() -> None:
    """Trois nombres sans rapport — une référence, une année, un code — ne sont pas
    une ligne de facturation. Sans en-tête ni ligne cohérente, on se tait."""
    markdown = "| Réf 447 | 2019 | 69003 | 12 |\n"

    assert METHOD_ARITHMETIC not in methodes(analyse_markdown(markdown))


def test_un_tableau_sans_en_tete_explicite_se_calibre_sur_ses_lignes() -> None:
    """Une ligne cohérente suffit à établir que le tableau est multiplicatif,
    et la ligne fausse ressort alors même sans en-tête reconnaissable."""
    markdown = "| Vis | 3 | 10,00 | 30,00 |\n| Clous | 4 | 10,00 | 45,00 |\n"

    blocks = [b for b in analyse_markdown(markdown) if b.method == METHOD_ARITHMETIC]

    assert len(blocks) == 1
    assert "Clous" in extraits(blocks, markdown)[0]
