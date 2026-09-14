"""Vignettes des pages, pour la galerie de l'écran de validation.

Une page prétraitée pèse de l'ordre du mégaoctet et la galerie en affiche des
dizaines : la vignette est réduite et réencodée en JPEG avant de quitter l'API.
Elle n'est jamais stockée — elle se dérive de l'image en une fraction de seconde,
et un fichier de plus par page serait un fichier de plus à supprimer avec le
document.
"""

from pathlib import Path

import cv2

JPEG_QUALITY = 80


class UnreadableImageError(ValueError):
    """Le fichier existe, mais OpenCV n'en tire aucune image."""


def render_thumbnail(path: Path, width: int) -> bytes:
    """JPEG de `width` pixels de large, proportions gardées, jamais agrandi.

    Opération bloquante (lecture disque, décodage) : à appeler hors de la boucle
    asynchrone.
    """
    # IMREAD_COLOR : 8 bits sur trois canaux, ce que JPEG sait encoder — une
    # couche alpha ou une image 16 bits ferait échouer l'encodage.
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise UnreadableImageError(f"image illisible : {path.name}")

    height, current_width = image.shape[:2]
    if current_width > width:
        target_height = max(1, round(height * width / current_width))
        image = cv2.resize(image, (width, target_height), interpolation=cv2.INTER_AREA)

    encoded, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not encoded:
        raise UnreadableImageError(f"image impossible à réencoder : {path.name}")
    return buffer.tobytes()
