"""Prétraitement image (OpenCV).

Levier prioritaire du projet : les documents sources sont dégradés, et la qualité
de l'image conditionne davantage la sortie OCR que le choix du modèle vision.

Ordre d'application attendu : niveaux de gris → débruitage → correction
d'inclinaison (deskew) → contraste / binarisation adaptative.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PreprocessingResult:
    """Résultat immuable : l'image source n'est jamais modifiée sur place."""

    output_path: Path
    # Angle de redressement appliqué, en degrés. Utile pour diagnostiquer une
    # sortie OCR médiocre : un deskew qui dérape se voit ici.
    deskew_angle: float
    steps_applied: tuple[str, ...]


def preprocess_page(source: Path, destination: Path) -> PreprocessingResult:
    """Nettoie une image de page et écrit le résultat dans `destination`.

    Args:
        source: image brute, telle que scannée.
        destination: chemin d'écriture de l'image nettoyée.

    Returns:
        Le résultat, incluant l'angle de redressement et les étapes appliquées.
    """
    raise NotImplementedError("Prétraitement OpenCV — à implémenter")


def estimate_skew_angle(image_path: Path) -> float:
    """Estime l'inclinaison d'une page, en degrés."""
    raise NotImplementedError("Estimation d'inclinaison — à implémenter")
