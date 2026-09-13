"""Mesures d'exactitude d'une transcription.

- Le **taux d'erreur par caractère** dit si le texte est globalement lisible.
- L'**exactitude des nombres** dit si ce qui compte a été lu juste : `28,60` pour
  `28,90` ne coûte qu'un caractère et fausse une ligne entière.

Les deux textes subissent la même normalisation de mise en page : les tirets de
remplissage et les coupures de fin de ligne relèvent de la machine à écrire, pas
du contenu, et les compter noierait les vraies erreurs.
"""

import re
from collections import Counter
from dataclasses import dataclass

_MOT_COUPE = re.compile(r"(\w)-[ \t]*\n\s*(\w)")
_TIRETS_REMPLISSAGE = re.compile(r"-{2,}")
_ESPACES = re.compile(r"\s+")

# Un nombre commence par un chiffre ou le `I` tapé pour un `1`. Il se prolonge
# par des chiffres, un séparateur suivi d'un chiffre, ou une espace de milliers
# suivie d'exactement trois chiffres — ce qui évite de fusionner « lots 6 et 28 ».
_NOMBRE = re.compile(
    r"[0-9I](?:[0-9IO]|[.,/](?=[0-9IO])"
    r"|[ \u00a0\u202f](?=[0-9IO]{3}(?![0-9IO])))*"
)
_CHIFFRE = re.compile(r"[0-9]")
_SEPARATEURS_MILLIERS = re.compile(r"[. \u00a0\u202f]")


def normaliser_mise_en_page(texte: str) -> str:
    """Recolle les mots coupés, retire les tirets de remplissage, uniformise les espaces."""
    texte = _MOT_COUPE.sub(r"\1\2", texte)
    texte = _TIRETS_REMPLISSAGE.sub(" ", texte)
    return _ESPACES.sub(" ", texte).strip()


def distance_edition(a: str, b: str) -> int:
    """Distance de Levenshtein, sur deux lignes de programmation dynamique."""
    if len(a) < len(b):
        a, b = b, a
    precedente = list(range(len(b) + 1))
    for i, car_a in enumerate(a, start=1):
        courante = [i]
        for j, car_b in enumerate(b, start=1):
            courante.append(
                min(
                    precedente[j] + 1,
                    courante[j - 1] + 1,
                    precedente[j - 1] + (car_a != car_b),
                )
            )
        precedente = courante
    return precedente[-1]


def taux_erreur_caracteres(reference: str, hypothese: str) -> float:
    """Erreurs rapportées à la longueur de la référence, mise en page normalisée."""
    attendu = normaliser_mise_en_page(reference)
    if not attendu:
        raise ValueError("référence vide : aucun taux d'erreur ne peut en être tiré")
    return distance_edition(attendu, normaliser_mise_en_page(hypothese)) / len(attendu)


def normaliser_nombre(nombre: str) -> str:
    """Forme comparable : `I` et `O` tapés valent `1` et `0`, milliers sans séparateur."""
    chiffres = nombre.replace("I", "1").replace("O", "0")
    return _SEPARATEURS_MILLIERS.sub("", chiffres)


def nombres(texte: str) -> Counter[str]:
    """Les nombres d'un texte, normalisés et comptés.

    Un jeton sans vrai chiffre (« I », « II », « IO » dans un mot) n'en est pas un.
    """
    return Counter(
        normaliser_nombre(jeton) for jeton in _NOMBRE.findall(texte) if _CHIFFRE.search(jeton)
    )


@dataclass(frozen=True)
class ScoreNombres:
    attendus: int
    lus: int
    justes: int

    @property
    def rappel(self) -> float | None:
        """Part des nombres attendus effectivement lus ; absent s'il n'y en avait aucun."""
        return self.justes / self.attendus if self.attendus else None

    @property
    def precision(self) -> float | None:
        """Part des nombres lus qui étaient attendus ; absent si rien n'a été lu."""
        return self.justes / self.lus if self.lus else None


def exactitude_nombres(reference: str, hypothese: str) -> ScoreNombres:
    attendus = nombres(reference)
    lus = nombres(hypothese)
    return ScoreNombres(
        attendus=attendus.total(),
        lus=lus.total(),
        justes=(attendus & lus).total(),
    )
