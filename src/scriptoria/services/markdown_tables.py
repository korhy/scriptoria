"""Lecture des tableaux Markdown produits par l'OCR, offsets compris.

Isolé de `confidence.py` parce que deux étapes en dépendent : la cohérence
arithmétique aujourd'hui, le découpage par section demain. Les offsets sont
conservés de bout en bout — un bloc de confiance repère un fragment par sa
position dans le Markdown, et une position approximative surlignerait le mauvais
passage dans l'UI de validation.

Le balisage analysé vient d'un modèle, pas d'un générateur : il est parfois
irrégulier. On rend ce qu'on voit, on n'exige pas du Markdown canonique — c'est
précisément l'irrégularité qui porte le signal.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

__all__ = ["Cell", "Row", "Table", "parse_number", "parse_tables"]

# Une ligne de séparation : | --- | :---: | ... Aucune donnée à y lire.
_SEPARATOR = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")

# Espaces fines et insécables comprises : un modèle en produit dans les montants.
_THOUSANDS = re.compile("[\\s\\u00a0\\u202f\\u2009]")
_NUMBER_ONLY = re.compile(r"^[-+]?\d+(?:[.,]\d+)*$")
_CURRENCY = str.maketrans("", "", "€$£%")


@dataclass(frozen=True)
class Cell:
    """Une cellule et sa position exacte dans le Markdown source."""

    text: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class Row:
    cells: tuple[Cell, ...]
    start_offset: int
    end_offset: int

    @property
    def texts(self) -> tuple[str, ...]:
        return tuple(cell.text for cell in self.cells)


@dataclass(frozen=True)
class Table:
    """Un bloc de lignes contiguës portant des pipes.

    `header` est absent quand le bloc n'a pas de ligne de séparation : le modèle
    rend parfois un total isolé (`Total HT | 311,40 |`) qui n'est pas un tableau
    en bonne et due forme mais qu'on ne veut pas perdre.
    """

    header: Row | None
    rows: tuple[Row, ...]


def parse_number(text: str) -> Decimal | None:
    """Lit une cellule numérique. `None` dès que ce n'est pas *uniquement* un nombre.

    Retourner 0 pour « Désignation », ou 6 pour « 6 cartouches », ferait échouer
    la vérification arithmétique en aval sur des cellules qu'elle n'aurait jamais
    dû comparer.

    Convention française : la virgule est décimale. Un point isolé est traité
    comme décimal lui aussi (`4.50`), ce que produit parfois le modèle.
    """
    candidate = _THOUSANDS.sub("", text.translate(_CURRENCY).strip())
    if not _NUMBER_ONLY.match(candidate):
        return None

    # Deux séparateurs : le dernier est le décimal, l'autre marquait les milliers.
    if "," in candidate and "." in candidate:
        decimal_sep = max(candidate.rfind(","), candidate.rfind("."))
        entier = re.sub(r"[.,]", "", candidate[:decimal_sep])
        candidate = f"{entier}.{candidate[decimal_sep + 1 :]}"
    else:
        candidate = candidate.replace(",", ".")
        if candidate.count(".") > 1:
            # 1.234.567 : des milliers, aucune décimale.
            candidate = candidate.replace(".", "")

    try:
        return Decimal(candidate)
    except InvalidOperation:
        return None


def _parse_row(line: str, line_offset: int) -> Row:
    """Découpe une ligne sur ses pipes, en gardant la position de chaque cellule."""
    cells: list[Cell] = []
    position = 0
    # Le découpage conserve les bornes vides des pipes extérieurs, qu'on écarte.
    fragments = line.split("|")
    for index, fragment in enumerate(fragments):
        start = position
        position += len(fragment) + 1

        borne_exterieure = index in (0, len(fragments) - 1)
        if borne_exterieure and not fragment.strip():
            continue

        stripped = fragment.strip()
        decalage = fragment.index(stripped) if stripped else 0
        cells.append(
            Cell(
                text=stripped,
                start_offset=line_offset + start + decalage,
                end_offset=line_offset + start + decalage + len(stripped),
            )
        )

    return Row(
        cells=tuple(cells),
        start_offset=line_offset + (len(line) - len(line.lstrip())),
        end_offset=line_offset + len(line.rstrip()),
    )


def _build_table(rows: list[Row], separators: list[int]) -> Table:
    """Assemble un bloc : la ligne précédant un séparateur en est l'en-tête."""
    if not separators:
        return Table(header=None, rows=tuple(rows))

    premier = separators[0]
    header = rows[premier - 1] if premier > 0 else None
    data = [row for index, row in enumerate(rows) if index not in separators and row is not header]
    return Table(header=header, rows=tuple(data))


def parse_tables(markdown: str) -> list[Table]:
    """Extrait les blocs de lignes portant des pipes, dans l'ordre du document."""
    tables: list[Table] = []
    bloc: list[Row] = []
    separators: list[int] = []
    offset = 0

    for line in markdown.splitlines(keepends=True):
        contenu = line.rstrip("\n")
        if "|" in contenu:
            if _SEPARATOR.match(contenu):
                separators.append(len(bloc))
            bloc.append(_parse_row(contenu, offset))
        elif bloc:
            tables.append(_build_table(bloc, separators))
            bloc, separators = [], []
        offset += len(line)

    if bloc:
        tables.append(_build_table(bloc, separators))
    return [table for table in tables if table.rows]
