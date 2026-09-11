"""Indice de confiance sur une transcription.

**Point ouvert — non tranché.** Un LLM vision ne fournit pas de confiance native
exploitable. Trois signaux sont candidats, aucun n'est suffisant seul :

1. Score déclaratif du modèle par bloc — peu fiable isolément, le modèle est
   mal calibré sur sa propre incertitude.
2. Double passage (deux exécutions, ou deux modèles) puis mesure de divergence
   entre les sorties. Coûteux : double le temps d'OCR. **Hypothèse par défaut.**
3. Signal objectif complémentaire : perplexité, ou score Tesseract en secours sur
   les zones douteuses.

`method` est conservé sur chaque bloc en base précisément pour pouvoir comparer
ces approches sur les mêmes documents avant d'en retenir une.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfidenceBlock:
    """Score sur un fragment, repéré par offsets de caractères dans le Markdown."""

    start_offset: int
    end_offset: int
    score: float
    method: str


def compare_passes(first: str, second: str) -> list[ConfidenceBlock]:
    """Dérive une confiance par bloc de la divergence entre deux passages OCR.

    Deux sorties concordantes sur un fragment indiquent une transcription stable ;
    une divergence signale une zone à faire relire.
    """
    raise NotImplementedError("Double passage — méthode à trancher puis implémenter")


def aggregate_page_score(blocks: list[ConfidenceBlock]) -> float:
    """Agrège les scores de blocs en un score de page, dans [0, 1].

    L'agrégation ne doit pas être une simple moyenne : un unique bloc très
    incertain doit faire chuter le score de la page, sans quoi l'UI de validation
    laisserait passer des pages partiellement fausses.
    """
    raise NotImplementedError("Agrégation — à implémenter")
