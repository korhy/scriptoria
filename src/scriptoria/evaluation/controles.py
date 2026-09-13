"""Contrôles objectifs : le document se vérifie lui-même, sans texte de référence.

Les tables de tantièmes se somment à un total connu. Elles sont coupées sur
plusieurs pages, et deux tables de même dénominateur se suivent parfois sur une
même page : les fractions sont donc suivies dans l'ordre de lecture, et une table
se termine sur sa ligne de total (numérateur égal au dénominateur).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from scriptoria.evaluation.corpus import ControleNombres, ControleTables
from scriptoria.evaluation.mesures import nombres, normaliser_nombre

_FRACTION = re.compile(
    r"(?<![0-9IO])([0-9IO](?:[0-9IO]|[.,](?=[0-9IO]))*)"
    r"\s*/\s*"
    r"([0-9IO](?:[\s.,]?[0-9IO]){3})(?![0-9IO])"
)
_ESPACES = re.compile(r"\s")


def _entier(jeton: str) -> int:
    return int(normaliser_nombre(_ESPACES.sub("", jeton)).replace(",", ""))


def fractions(texte: str, denominateur: int) -> list[int]:
    """Numérateurs des fractions `n/denominateur`, dans l'ordre du texte."""
    return [
        _entier(numerateur)
        for numerateur, bas in _FRACTION.findall(texte)
        if _entier(bas) == denominateur
    ]


@dataclass(frozen=True)
class ResultatTable:
    nom: str
    lots_attendus: int
    lots_lus: int
    somme: int
    total_attendu: int
    total_lu: bool
    pages_manquantes: tuple[int, ...]

    @property
    def juste(self) -> bool:
        return (
            not self.pages_manquantes
            and self.total_lu
            and self.lots_lus == self.lots_attendus
            and self.somme == self.total_attendu
        )


def _segments(valeurs: list[int], denominateur: int) -> list[tuple[list[int], bool]]:
    """Découpe la suite des fractions en tables, chacune close par sa ligne de total."""
    segments: list[tuple[list[int], bool]] = []
    courant: list[int] = []
    for valeur in valeurs:
        if valeur == denominateur:
            segments.append((courant, True))
            courant = []
        else:
            courant.append(valeur)
    if courant:
        segments.append((courant, False))
    return segments


def verifier_tables(
    textes: Mapping[int, str], controle: ControleTables
) -> tuple[ResultatTable, ...]:
    """Reconstitue les tables d'un contrôle à partir des pages transcrites.

    Une page non transcrite est signalée, jamais supposée vide : une table à
    laquelle il manque une page n'est pas fausse, elle est invérifiable.
    """
    pages = range(controle.premiere_page, controle.derniere_page + 1)
    manquantes = tuple(page for page in pages if page not in textes)
    valeurs = [
        valeur
        for page in pages
        if page in textes
        for valeur in fractions(textes[page], controle.denominateur)
    ]
    segments = _segments(valeurs, controle.denominateur)

    resultats = []
    for index, attendue in enumerate(controle.tables):
        lots, total_lu = segments[index] if index < len(segments) else ([], False)
        resultats.append(
            ResultatTable(
                nom=attendue.nom,
                lots_attendus=attendue.lots,
                lots_lus=len(lots),
                somme=sum(lots),
                total_attendu=attendue.total,
                total_lu=total_lu,
                pages_manquantes=manquantes,
            )
        )
    return tuple(resultats)


@dataclass(frozen=True)
class ResultatNombres:
    nom: str
    page: int
    trouves: tuple[str, ...]
    manquants: tuple[str, ...]
    page_manquante: bool

    @property
    def juste(self) -> bool:
        return not self.page_manquante and not self.manquants


def verifier_nombres_attendus(
    textes: Mapping[int, str], controle: ControleNombres
) -> ResultatNombres:
    """Les nombres attendus sur une page ont-ils tous été lus ?"""
    if controle.page not in textes:
        return ResultatNombres(
            controle.nom, controle.page, (), tuple(controle.nombres), page_manquante=True
        )

    disponibles = nombres(textes[controle.page])
    trouves, manquants = [], []
    for attendu in controle.nombres:
        cle = normaliser_nombre(attendu)
        if disponibles[cle] > 0:
            disponibles[cle] -= 1
            trouves.append(attendu)
        else:
            manquants.append(attendu)
    return ResultatNombres(
        controle.nom, controle.page, tuple(trouves), tuple(manquants), page_manquante=False
    )
