"""Remplacements de texte qui gardent la trace des positions.

Les blocs de confiance d'une révision sont repérés par offsets de caractères. Une
mise en forme qui déplace le texte sans dire où chaque caractère est parti ferait
surligner la mauvaise ligne — ou perdrait l'alerte d'une page douteuse.
"""

import pytest

from scriptoria.services.text_edits import Edit, Rewrite, apply_edits, remap_span


def test_sans_remplacement_le_texte_et_les_positions_sont_inchanges() -> None:
    rewrite = apply_edits("abc", [])

    assert rewrite.text == "abc"
    assert rewrite.positions == (0, 1, 2, 3)


def test_un_remplacement_decale_les_positions_qui_le_suivent() -> None:
    # « exis-\ntence » → « existence » : le tiret et le saut de ligne tombent.
    rewrite = apply_edits("exis-\ntence", [Edit(4, 6, "")])

    assert rewrite.text == "existence"
    assert rewrite.positions[6] == 4  # le « t » de « tence »
    assert rewrite.positions[11] == 9  # la fin du texte


def test_un_caractere_supprime_pointe_la_ou_il_aurait_ete() -> None:
    rewrite = apply_edits("a--b", [Edit(1, 3, "-")])

    assert rewrite.text == "a-b"
    assert rewrite.positions[1] == 1
    assert rewrite.positions[2] == 1


def test_les_remplacements_sont_appliques_dans_l_ordre_du_texte() -> None:
    rewrite = apply_edits("a,b,c", [Edit(3, 4, ", "), Edit(1, 2, ", ")])

    assert rewrite.text == "a, b, c"


def test_des_remplacements_qui_se_chevauchent_sont_refuses() -> None:
    """Deux règles qui réécrivent le même caractère : le résultat dépendrait de l'ordre."""
    with pytest.raises(ValueError, match="chevauch"):
        apply_edits("abcdef", [Edit(1, 4, ""), Edit(3, 5, "")])


def test_deux_reecritures_successives_se_composent() -> None:
    premiere = apply_edits("a-\nb, c", [Edit(1, 3, "")])  # « ab, c »
    seconde = apply_edits(premiere.text, [Edit(0, 0, "> ")])  # « > ab, c »

    composee = premiere.then(seconde)

    assert composee.text == "> ab, c"
    assert composee.positions[3] == 3  # « b » : 3 → 1 → 3
    assert len(composee.positions) == len("a-\nb, c") + 1


def test_une_etendue_suit_le_texte_qu_elle_couvre() -> None:
    texte = "ligne-\nsuivante 28,60 fin"
    rewrite = apply_edits(texte, [Edit(5, 7, "")])
    debut = texte.index("28,60")

    nouveau_debut, nouvelle_fin = remap_span(rewrite, debut, debut + 5)

    assert rewrite.text[nouveau_debut:nouvelle_fin] == "28,60"


def test_la_reecriture_identite_ne_change_rien() -> None:
    identite = Rewrite.identity("abc")

    assert identite.then(apply_edits("abc", [Edit(0, 1, "A")])).text == "Abc"
