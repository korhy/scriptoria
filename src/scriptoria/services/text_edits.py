"""Remplacements de texte qui gardent la trace des positions.

Les blocs de confiance d'une révision sont repérés par offsets de caractères dans
son Markdown. Réécrire ce Markdown sans savoir où chaque caractère est parti
ferait surligner la mauvaise ligne, ou perdrait l'alerte d'une page douteuse. Une
`Rewrite` porte donc, avec le texte produit, la position de chaque caractère
d'origine dans ce texte.
"""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Edit:
    """Remplace `text[start:end]` par `replacement`. `start == end` insère."""

    start: int
    end: int
    replacement: str


@dataclass(frozen=True)
class Rewrite:
    """Texte produit, et où est allé chaque caractère du texte d'origine.

    `positions[i]` est l'offset, dans `text`, du caractère `i` d'origine — ou de
    l'endroit où il aurait été s'il a été supprimé. Une entrée de plus désigne la
    fin du texte, pour qu'une étendue `[début, fin)` se reporte sans cas particulier.
    """

    text: str
    positions: tuple[int, ...]

    @classmethod
    def identity(cls, text: str) -> "Rewrite":
        return cls(text=text, positions=tuple(range(len(text) + 1)))

    def then(self, other: "Rewrite") -> "Rewrite":
        """Enchaîne `other`, qui doit partir du texte produit par cette réécriture."""
        if len(other.positions) != len(self.text) + 1:
            raise ValueError("la seconde réécriture ne part pas du texte produit par la première")
        return Rewrite(
            text=other.text,
            positions=tuple(other.positions[position] for position in self.positions),
        )


def apply_edits(text: str, edits: Iterable[Edit]) -> Rewrite:
    """Applique des remplacements disjoints, dans l'ordre du texte.

    Raises:
        ValueError: deux remplacements se chevauchent, ou l'un sort du texte. Le
            résultat dépendrait sinon de l'ordre dans lequel les règles ont écrit.
    """
    pieces: list[str] = []
    positions: list[int] = []
    cursor = 0
    length = 0

    for edit in sorted(edits, key=lambda edit: (edit.start, edit.end)):
        if not 0 <= edit.start <= edit.end <= len(text):
            raise ValueError(f"remplacement hors du texte : [{edit.start}, {edit.end})")
        if edit.start < cursor:
            raise ValueError(f"remplacements qui se chevauchent à l'offset {edit.start}")

        kept = text[cursor : edit.start]
        positions.extend(range(length, length + len(kept)))
        pieces.append(kept)
        length += len(kept)

        positions.extend([length] * (edit.end - edit.start))
        pieces.append(edit.replacement)
        length += len(edit.replacement)
        cursor = edit.end

    tail = text[cursor:]
    positions.extend(range(length, length + len(tail)))
    pieces.append(tail)
    positions.append(length + len(tail))

    return Rewrite(text="".join(pieces), positions=tuple(positions))


def remap_span(rewrite: Rewrite, start: int, end: int) -> tuple[int, int]:
    """Reporte une étendue du texte d'origine dans le texte réécrit."""
    return rewrite.positions[start], rewrite.positions[end]
