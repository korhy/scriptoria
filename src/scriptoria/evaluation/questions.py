"""Évaluation de la recherche et de la réponse générée."""

import re
from collections.abc import Collection, Mapping, Sequence
from typing import Any

_SEPARATEUR_CHIFFRES = re.compile(r"(?<=\d)[ .\u00a0\u202f](?=\d)")
_ESPACES = re.compile(r"\s+")


def rang_premiere_page(
    hits: Sequence[Mapping[str, Any]], pages_attendues: Collection[int]
) -> int | None:
    """Rang (à partir de 1) du premier passage tiré d'une page attendue."""
    for rang, hit in enumerate(hits, start=1):
        if hit["page_number"] in pages_attendues:
            return rang
    return None


def rappel_a_k(rangs: Sequence[int | None], k: int) -> float:
    """Part des questions dont une page attendue figure dans les `k` premiers passages."""
    if not rangs:
        raise ValueError("rappel calculé sur aucune question")
    return sum(1 for rang in rangs if rang is not None and rang <= k) / len(rangs)


def _normaliser(texte: str) -> str:
    return _ESPACES.sub(" ", _SEPARATEUR_CHIFFRES.sub("", texte.lower()))


def reponse_contient(reponse: str, valeurs: Sequence[str]) -> str | None:
    """La première valeur acceptée présente dans la réponse, ou `None`.

    Casse et séparateurs de milliers ignorés : `7 700 000` vaut `7.700.000`.
    """
    normalisee = _normaliser(reponse)
    for valeur in valeurs:
        if _normaliser(valeur) in normalisee:
            return valeur
    return None
