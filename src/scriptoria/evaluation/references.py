"""Références d'évaluation : pages saisies en fichier, ou relues dans l'UI.

Une relecture validée dans l'UI est une référence : c'est le texte qu'un humain a
approuvé, et c'est ce que les révisions immuables devaient permettre de mesurer.
Mais elle part du texte de l'OCR : l'œil y laisse passer ce que le modèle a bien
imité, et le taux d'erreur qu'elle donne est un **minimum**. Une page saisie en
fichier, sans partir de l'OCR, prime donc toujours, et chaque référence garde son
origine jusque dans le rapport.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

SAISIE = "saisie"
RELECTURE = "relecture"


@dataclass(frozen=True)
class Reference:
    texte: str
    origine: str


def derniere_revision(
    revisions: Sequence[Mapping[str, Any]], *, origine: str, validee: bool = False
) -> str | None:
    """Texte de la révision de plus haut numéro pour cette origine, ou `None`.

    `validee` écarte les révisions non validées : un brouillon enregistré sans
    validation n'engage pas le relecteur. Elle écarte aussi les validations **en
    lot** : valider d'un clic toutes les pages d'un document n'est pas les relire,
    et le texte d'OCR ainsi approuvé donnerait 0 % d'erreur là où personne n'a
    regardé.
    """
    candidates = [
        revision
        for revision in revisions
        if revision["origin"] == origine
        and (not validee or (revision["is_validated"] and not revision["bulk_validated"]))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda revision: revision["revision"])["content_markdown"]


def fusionner_references(
    saisies: Mapping[int, str], relectures: Mapping[int, str]
) -> Mapping[int, Reference]:
    """Une référence par page, rangées par numéro ; la saisie prime sur la relecture.

    Une relecture vide est écartée : aucun taux d'erreur ne se calcule sur rien.
    """
    references = {
        page: Reference(texte, RELECTURE) for page, texte in relectures.items() if texte.strip()
    }
    references.update({page: Reference(texte, SAISIE) for page, texte in saisies.items()})
    return MappingProxyType(dict(sorted(references.items())))
