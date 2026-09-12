"""Indice de confiance sur une transcription.

**Approche retenue le 2026-09-12 : des signaux objectifs d'abord, le double
passage seulement en secours.** La mesure du 2026-09-11 a tranché la question de
coût : un double passage systématique double le temps d'OCR (~114 s/page, soit
6 h pour 200 pages au lieu de 3). Or le mode de défaillance à redouter ici n'est
pas la sortie visiblement cassée, c'est le **chiffre plausible et faux** — `28,60`
lu au lieu de `28,90`, total de ligne intact, ligne arithmétiquement fausse sans
que rien ne paraisse anormal.

Ce défaut-là se démontre sans dépenser une seconde de GPU :

1. `arithmetic` — cohérence interne des tableaux chiffrés. Vérifiable, gratuit,
   et c'est exactement le signal qui attrape le cas mesuré.
2. `structural` — cellule manquante, ligne plus courte que l'en-tête, passage
   déclaré illisible par le modèle, caractère de remplacement.
3. `double_pass` — divergence entre deux passages. Conservé, mais **déclenché
   sélectivement** par le worker sur les seules pages dont le score est faible.

Le score déclaratif du modèle n'est pas utilisé : un LLM vision est mal calibré
sur sa propre incertitude, et une cohérence arithmétique est une preuve là où un
score est une opinion.

`method` reste conservé sur chaque bloc en base : ces trois méthodes doivent
pouvoir être comparées sur les mêmes documents, et l'affaire n'est pas close.

**Ce que ce score n'est pas.** Une page sans bloc vaut 1.0, ce qui signifie
« aucun signal d'alerte », jamais « transcription exacte » : une page de texte
libre n'offre aucune prise à ces vérifications. La validation reste humaine.
"""

import difflib
import re
from dataclasses import dataclass
from decimal import Decimal

from scriptoria.services.markdown_tables import Cell, Row, Table, parse_number, parse_tables

__all__ = [
    "METHOD_ARITHMETIC",
    "METHOD_DOUBLE_PASS",
    "METHOD_STRUCTURAL",
    "ConfidenceBlock",
    "aggregate_page_score",
    "analyse_markdown",
    "compare_passes",
]

METHOD_ARITHMETIC = "arithmetic"
METHOD_STRUCTURAL = "structural"
# Bandit lit « pass » comme un mot de passe (S105). C'est le nom du domaine —
# « double passage » — et il est stocké tel quel dans la colonne `method`.
METHOD_DOUBLE_PASS = "double_pass"  # noqa: S105

# Scores nommés : ils encodent une hiérarchie de gravité, pas des réglages.
# Une incohérence arithmétique est une quasi-certitude d'erreur ; une cellule
# vide, un doute.
SCORE_ARITHMETIC_MISMATCH = 0.15
SCORE_ILLEGIBLE = 0.2
SCORE_REPLACEMENT_CHAR = 0.3
SCORE_EMPTY_CELL = 0.4
SCORE_RAGGED_ROW = 0.4
# Une divergence qui porte sur des chiffres est grave même quand les deux
# passages sont textuellement presque identiques : c'est tout le propos ici.
SCORE_NUMERIC_DIVERGENCE = 0.2

# Un centime d'écart est un arrondi de facturation, pas une erreur de lecture.
ABSOLUTE_TOLERANCE = Decimal("0.02")
RELATIVE_TOLERANCE = Decimal("0.005")

_ILLEGIBLE = re.compile(r"\[illisible\]", re.IGNORECASE)
_REPLACEMENT = re.compile("�+")
_DIGITS = re.compile(r"\d+")
_WHITESPACE = re.compile(r"\s+")
_QUANTITY_HEADER = re.compile(r"qt[ée]|quantit|nombre|qty", re.IGNORECASE)
_TOTAL_HEADER = re.compile(r"total|montant|somme", re.IGNORECASE)
_TOTAL_LABEL = re.compile(r"^(total|somme|montant total)", re.IGNORECASE)


@dataclass(frozen=True)
class ConfidenceBlock:
    """Score sur un fragment, repéré par offsets de caractères dans le Markdown."""

    start_offset: int
    end_offset: int
    score: float
    method: str


def _row_block(row: Row, score: float, method: str) -> ConfidenceBlock:
    return ConfidenceBlock(
        start_offset=row.start_offset, end_offset=row.end_offset, score=score, method=method
    )


def _matches(expected: Decimal, actual: Decimal) -> bool:
    """Égalité au centime près, ou à 0,5 % pour les montants remisés ou arrondis."""
    gap = abs(expected - actual)
    if gap <= ABSOLUTE_TOLERANCE:
        return True
    return expected != 0 and gap / abs(expected) <= RELATIVE_TOLERANCE


def _numbers(row: Row) -> list[tuple[Cell, Decimal]]:
    """Cellules purement numériques de la ligne, dans l'ordre."""
    valued = ((cell, parse_number(cell.text)) for cell in row.cells)
    return [(cell, value) for cell, value in valued if value is not None]


def _is_consistent_line_item(values: list[Decimal]) -> bool:
    """Une ligne quantité x prix unitaire = total, dans n'importe quel ordre."""
    first, second, third = values
    return (
        _matches(first * second, third)
        or _matches(first * third, second)
        or _matches(second * third, first)
    )


def _looks_like_line_items(table: Table) -> bool:
    """L'en-tête annonce une quantité et un total : les lignes sont multiplicatives."""
    if table.header is None:
        return False
    header = " ".join(table.header.texts)
    return bool(_QUANTITY_HEADER.search(header) and _TOTAL_HEADER.search(header))


def _line_item_rows(table: Table) -> list[tuple[Row, list[tuple[Cell, Decimal]]]]:
    """Lignes à exactement trois nombres : les seules qu'on sache vérifier."""
    candidates = ((row, _numbers(row)) for row in table.rows)
    return [(row, numbers) for row, numbers in candidates if len(numbers) == 3]


def _is_line_item_table(table: Table) -> bool:
    """Ce tableau facture des lignes quantité x prix unitaire.

    Deux indices suffisent : son en-tête l'annonce, ou au moins une de ses lignes
    vérifie la relation. Sans ce garde-fou, trois nombres sans rapport dans une
    même ligne (une référence, une année, un code postal) seraient signalés à
    tort — le tableau se calibre ainsi sur lui-même.
    """
    rows = _line_item_rows(table)
    if not rows:
        return False
    if _looks_like_line_items(table):
        return True
    return any(_is_consistent_line_item([value for _, value in numbers]) for _, numbers in rows)


def _product_blocks(table: Table) -> list[ConfidenceBlock]:
    """Signale les lignes dont le produit ne donne pas le total affiché."""
    if not _is_line_item_table(table):
        return []

    rows = _line_item_rows(table)
    return [
        _row_block(row, SCORE_ARITHMETIC_MISMATCH, METHOD_ARITHMETIC)
        for row, numbers in rows
        if not _is_consistent_line_item([value for _, value in numbers])
    ]


def _stated_totals(tables: list[Table]) -> list[tuple[Row, Decimal]]:
    """Lignes annonçant un total : « Total HT | 311,40 », « Total TTC | 373,68 »."""
    totals: list[tuple[Row, Decimal]] = []
    for table in tables:
        for row in table.rows:
            numbers = _numbers(row)
            if len(numbers) == 1 and row.cells and _TOTAL_LABEL.match(row.cells[0].text):
                totals.append((row, numbers[0][1]))
    return totals


def _sum_blocks(tables: list[Table]) -> list[ConfidenceBlock]:
    """Signale les totaux qu'aucune somme de lignes ne justifie.

    Un document porte plusieurs totaux (HT, TVA, TTC) : il suffit qu'**un** d'eux
    corresponde à la somme des lignes pour que l'ensemble soit cohérent. Aucun
    ne correspond, en revanche, et c'est soit une ligne soit un total qui a été
    mal lu.
    """
    # Toutes les lignes, y compris celles déjà signalées : une ligne dont le
    # prix unitaire a été mal lu garde souvent un total juste, et l'exclure de
    # la somme ferait échouer ce contrôle-ci par ricochet.
    amounts = [
        numbers[-1][1]
        for table in tables
        if _is_line_item_table(table)
        for _, numbers in _line_item_rows(table)
    ]
    totals = _stated_totals(tables)
    if len(amounts) < 2 or not totals:
        return []

    expected = sum(amounts, Decimal(0))
    if any(_matches(expected, stated) for _, stated in totals):
        return []

    return [_row_block(row, SCORE_ARITHMETIC_MISMATCH, METHOD_ARITHMETIC) for row, _ in totals]


def _pattern_blocks(markdown: str) -> list[ConfidenceBlock]:
    """Marqueurs d'illisibilité dans le texte, tableaux ou non."""
    blocks = [
        ConfidenceBlock(
            start_offset=match.start(),
            end_offset=match.end(),
            score=score,
            method=METHOD_STRUCTURAL,
        )
        for pattern, score in (
            (_ILLEGIBLE, SCORE_ILLEGIBLE),
            (_REPLACEMENT, SCORE_REPLACEMENT_CHAR),
        )
        for match in pattern.finditer(markdown)
    ]
    return blocks


def _table_shape_blocks(tables: list[Table]) -> list[ConfidenceBlock]:
    """Cellule vide ou ligne amputée : une colonne perdue décale tout le reste.

    Le bloc couvre la ligne, pas la cellule : une cellule vide n'a pas d'étendue
    à surligner, et c'est la ligne entière qui devient douteuse.
    """
    blocks: list[ConfidenceBlock] = []
    for table in tables:
        width = len(table.header.cells) if table.header is not None else None
        for row in table.rows:
            if width is not None and len(row.cells) != width:
                blocks.append(_row_block(row, SCORE_RAGGED_ROW, METHOD_STRUCTURAL))
            elif any(not cell.text for cell in row.cells):
                blocks.append(_row_block(row, SCORE_EMPTY_CELL, METHOD_STRUCTURAL))
    return blocks


def analyse_markdown(markdown: str) -> list[ConfidenceBlock]:
    """Dérive les blocs de confiance d'une transcription, sans appeler de modèle.

    Gratuit : aucune inférence, aucun second passage. C'est ce qui permet de
    l'appliquer à toutes les pages et de réserver le double passage aux suspectes.
    """
    tables = parse_tables(markdown)
    blocks = _pattern_blocks(markdown) + _table_shape_blocks(tables) + _sum_blocks(tables)
    for table in tables:
        blocks.extend(_product_blocks(table))
    return sorted(blocks, key=lambda block: (block.start_offset, block.method))


def aggregate_page_score(blocks: list[ConfidenceBlock]) -> float:
    """Score de page dans [0, 1] : le **minimum** des blocs, pas leur moyenne.

    Une moyenne noierait un bloc très incertain dans une page par ailleurs
    propre, et l'UI de validation laisserait passer une page partiellement
    fausse — le seul défaut qu'on ne veut pas rater. Le nombre de blocs reste
    consultable à côté du score pour hiérarchiser les pages entre elles.
    """
    if not blocks:
        return 1.0
    return max(0.0, min(1.0, min(block.score for block in blocks)))


def _strip_layout(line: str) -> str:
    """Réduit une ligne à son contenu, mise en page exclue.

    Observé sur deux passages réels de `qwen2.5vl:7b` : la même ligne revient
    espacée différemment (`Échéance : 30 jours` puis `Échéance: 30 jours`, pipes
    alignés ou non). Compter cela comme une divergence remplirait la base de
    blocs sans contenu et ferait surligner des lignes justes dans l'UI.

    Tous les espaces tombent, pas seulement les répétitions : c'est le texte qu'on
    compare, et une divergence de coupure de mots n'est pas le défaut qu'on traque.
    """
    return _WHITESPACE.sub("", line)


def _divergence_score(first: str, second: str) -> float:
    """Similarité des deux fragments, plafonnée si les chiffres diffèrent."""
    ratio = difflib.SequenceMatcher(a=first, b=second, autojunk=False).ratio()
    if _DIGITS.findall(first) != _DIGITS.findall(second):
        return min(ratio, SCORE_NUMERIC_DIVERGENCE)
    return ratio


def compare_passes(first: str, second: str) -> list[ConfidenceBlock]:
    """Dérive une confiance par bloc de la divergence entre deux passages OCR.

    Deux sorties concordantes sur un fragment indiquent une transcription stable ;
    une divergence signale une zone à faire relire. Les offsets portent sur
    `first` : c'est la révision qu'on conserve, donc la seule qu'on surlignera.

    Coûteux — un passage de plus, ~57 s par page — d'où son déclenchement
    sélectif par le worker plutôt que systématique.
    """
    first_lines = first.splitlines(keepends=True)
    second_lines = second.splitlines(keepends=True)

    starts = [0]
    for line in first_lines:
        starts.append(starts[-1] + len(line))

    blocks: list[ConfidenceBlock] = []
    # La comparaison ignore la mise en page, les offsets portent sur les lignes
    # d'origine : le dépouillement est ligne à ligne, les index coïncident.
    matcher = difflib.SequenceMatcher(
        a=[_strip_layout(line) for line in first_lines],
        b=[_strip_layout(line) for line in second_lines],
        autojunk=False,
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        # `insert` : le second passage a ajouté du texte, rien à surligner ici.
        if tag == "equal" or i1 == i2:
            continue

        region = "".join(first_lines[i1:i2])
        offset = starts[i1] + (len(region) - len(region.lstrip()))
        stripped = region.strip()
        blocks.append(
            ConfidenceBlock(
                start_offset=offset,
                end_offset=offset + len(stripped),
                score=_divergence_score(
                    _strip_layout(region), _strip_layout("".join(second_lines[j1:j2]))
                ),
                method=METHOD_DOUBLE_PASS,
            )
        )
    return blocks
