"""Prétraitement image (OpenCV).

Levier prioritaire du projet : les documents sources sont dégradés, et la qualité
de l'image conditionne davantage la sortie OCR que le choix du modèle vision.

Chaîne appliquée : niveaux de gris → débruitage → redressement → redimensionnement
→ contraste.

**Deux choix méritent d'être explicités, car ils s'écartent du pipeline OCR
classique.**

*Pas de binarisation par défaut.* Le pipeline habituel (Tesseract) binarise, parce
qu'un moteur OCR classique exige une image bitonale. Ici la cible est un LLM
vision, entraîné sur des images naturelles : une binarisation dure détruit
l'information de gradient dont il se sert. Le mode `adaptive_threshold` reste
disponible, mais `clahe` (égalisation de contraste locale) est le défaut. À
trancher par la mesure sur documents réels, pas par principe.

*Le redimensionnement fait partie du prétraitement.* Mesure du 2026-09-11 sur M4 :
sur 57 s d'OCR par page A4, **37 s sont l'encodage de l'image**. La résolution
d'entrée pèse donc plus sur le coût que tout réglage du modèle, et c'est une
décision qui se prend ici. `max_edge_px` est le principal bouton de réglage du
coût du pipeline.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

ContrastMode = Literal["none", "clahe", "adaptive_threshold"]

# L'estimation d'angle travaille sur une image réduite : chercher l'angle parmi
# 81 candidats à pleine résolution coûterait des secondes par page pour une
# précision identique. 800 px suffisent à résoudre l'interligne d'un A4.
_SKEW_ESTIMATION_MAX_EDGE = 800
_SKEW_SEARCH_DEGREES = 10.0
_SKEW_STEP_DEGREES = 0.25

# Proportion de pixels d'encre en deçà/au-delà de laquelle il n'y a rien à
# aligner : page blanche, ou scan saturé.
#
# Le plancher est volontairement très bas. Une page A4 réelle de texte dense ne
# porte que ~0,4 % de pixels d'encre une fois réduite à 800 px ; un seuil à 0,5 %
# rejetait donc des pages parfaitement redressables. Ce garde-fou ne sert qu'à
# écarter les cas dégénérés — c'est `_MIN_PEAK_RATIO` qui juge de la fiabilité.
_MIN_INK_RATIO = 0.0002
_MAX_INK_RATIO = 0.60

# Le score au meilleur angle doit dominer nettement le score médian. Sinon le
# profil de projection ne présente pas de pic : tourner relèverait du hasard.
_MIN_PEAK_RATIO = 1.25


@dataclass(frozen=True)
class PreprocessingOptions:
    """Réglages du prétraitement.

    `max_edge_px` est le principal levier de coût du pipeline OCR : le diviser
    par deux divise à peu près par quatre le nombre de tokens image à encoder.
    """

    max_edge_px: int = 1600
    denoise: bool = True
    deskew: bool = True
    contrast: ContrastMode = "clahe"


@dataclass(frozen=True)
class PreprocessingResult:
    """Résultat immuable : l'image source n'est jamais modifiée sur place."""

    output_path: Path
    # Angle de redressement appliqué, en degrés. Utile pour diagnostiquer une
    # sortie OCR médiocre : un deskew qui dérape se voit ici.
    deskew_angle: float
    steps_applied: tuple[str, ...]
    # (largeur, hauteur), en pixels.
    source_size: tuple[int, int]
    output_size: tuple[int, int]


# --- Primitives pures : tableau → tableau, aucune I/O -----------------------


def to_grayscale(image: np.ndarray) -> np.ndarray:
    """Ramène une image à un seul canal. Une image déjà grise traverse intacte."""
    if image.ndim == 2:
        return image
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def downscale_to_max_edge(image: np.ndarray, max_edge: int) -> np.ndarray:
    """Réduit l'image pour que son plus grand côté atteigne `max_edge`.

    N'agrandit jamais : interpoler n'ajoute aucune information et gonflerait le
    coût d'encodage OCR pour rien.
    """
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= max_edge:
        return image

    scale = max_edge / longest
    # INTER_AREA est le bon choix en réduction : il moyenne les pixels source,
    # ce qui atténue le bruit au lieu de l'échantillonner.
    return cv2.resize(
        image,
        (round(width * scale), round(height * scale)),
        interpolation=cv2.INTER_AREA,
    )


def denoise(image: np.ndarray) -> np.ndarray:
    """Atténue le bruit en préservant les traits.

    Filtre bilatéral plutôt que `fastNlMeansDenoising` : ce dernier donne de
    meilleurs résultats mais coûte plusieurs secondes par page, ce qui pèserait
    lourd sur un lot de plusieurs centaines de pages. Un flou gaussien, lui,
    effacerait les traits fins des documents dégradés.
    """
    return cv2.bilateralFilter(image, d=5, sigmaColor=50, sigmaSpace=50)


def enhance_contrast(image: np.ndarray, mode: ContrastMode = "clahe") -> np.ndarray:
    """Rehausse le contraste selon le mode demandé.

    `adaptive_threshold` binarise : utile pour un OCR classique, probablement
    nuisible pour un LLM vision. Voir l'en-tête du module.
    """
    if mode == "none":
        return image
    if mode == "clahe":
        return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(image)
    if mode == "adaptive_threshold":
        return cv2.adaptiveThreshold(
            image, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10
        )
    raise ValueError(f"mode de contraste inconnu: {mode!r}")


def _rotate(image: np.ndarray, angle: float, border_value: int) -> np.ndarray:
    """Tourne autour du centre, en conservant les dimensions."""
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    return cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border_value,
    )


def _projection_sharpness(binary: np.ndarray, angle: float) -> float:
    """Netteté du profil de projection horizontal à un angle donné.

    Quand les lignes de texte sont horizontales, la somme des pixels d'encre par
    ligne alterne brutalement entre creux (interlignes) et pics (texte). La somme
    des carrés des écarts successifs mesure cette alternance : elle est maximale
    à l'angle qui redresse la page.
    """
    rotated = _rotate(binary, angle, border_value=0)
    profile = rotated.sum(axis=1, dtype=np.float64)
    return float(np.square(np.diff(profile)).sum())


def estimate_skew_angle(image: np.ndarray) -> float:
    """Estime l'inclinaison d'une page, en degrés.

    **Convention de signe** : retourne l'inclinaison *de la page*, pas la
    rotation corrective. Une page penchée de +3° renvoie `+3.0` ; on la redresse
    en appliquant `-3.0`. Confondre les deux doublerait l'inclinaison au lieu de
    la corriger — d'où le test qui vérifie des angles connus dans les deux sens.

    Méthode : maximisation de la netteté du profil de projection. Plus robuste
    sur documents dégradés que `minAreaRect`, que le bruit et les bords noirs de
    scan font facilement déraper.

    Retourne `0.0` quand la page ne permet pas de conclure — page blanche, scan
    saturé, ou absence de pic net. **Ne pas tourner vaut mieux que tourner au
    hasard** : une rotation erronée dégrade l'OCR au lieu de l'aider.
    """
    gray = to_grayscale(image)
    small = downscale_to_max_edge(gray, _SKEW_ESTIMATION_MAX_EDGE)

    # Encre = 1, fond = 0. Otsu choisit le seuil, ce qui évite d'en coder un en dur.
    _, binary = cv2.threshold(small, 0, 1, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)

    ink_ratio = float(binary.mean())
    if not _MIN_INK_RATIO <= ink_ratio <= _MAX_INK_RATIO:
        return 0.0

    angles = np.arange(
        -_SKEW_SEARCH_DEGREES,
        _SKEW_SEARCH_DEGREES + _SKEW_STEP_DEGREES,
        _SKEW_STEP_DEGREES,
    )
    scores = np.array([_projection_sharpness(binary, angle) for angle in angles])

    median = float(np.median(scores))
    if median <= 0 or float(scores.max()) < median * _MIN_PEAK_RATIO:
        return 0.0

    # `angles` parcourt les rotations candidates : celle qui maximise la netteté
    # est la rotation *corrective*. L'inclinaison de la page en est l'opposé.
    # `0.0 +` normalise le -0.0 que produirait la négation de zéro.
    return 0.0 + -float(angles[int(scores.argmax())])


# --- Orchestration : seule fonction qui touche au disque --------------------


def preprocess_page(
    source: Path,
    destination: Path,
    options: PreprocessingOptions | None = None,
) -> PreprocessingResult:
    """Nettoie une image de page et écrit le résultat dans `destination`.

    L'image source n'est jamais modifiée : on doit toujours pouvoir rejouer le
    prétraitement depuis l'original, notamment lorsqu'on en changera les réglages.

    Args:
        source: image brute, telle que scannée.
        destination: chemin d'écriture de l'image nettoyée (dossiers créés au besoin).
        options: réglages ; valeurs par défaut si absent.

    Returns:
        Le résultat, incluant l'angle de redressement et les étapes appliquées.

    Raises:
        ValueError: si l'image est illisible, dans un format non reconnu, ou si
            l'écriture échoue.
    """
    options = options or PreprocessingOptions()

    image = cv2.imread(str(source), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"image illisible ou format non reconnu: {source}")

    source_height, source_width = image.shape[:2]
    steps: list[str] = []

    image = to_grayscale(image)
    steps.append("grayscale")

    if options.denoise:
        image = denoise(image)
        steps.append("denoise")

    angle = 0.0
    if options.deskew:
        angle = estimate_skew_angle(image)
        if angle == 0.0:
            # Aucune rotation appliquée : page déjà droite, ou inclinaison
            # indéterminable. Distingué de "deskew" pour que `steps_applied`
            # dise ce qui a réellement eu lieu.
            steps.append("deskew_skipped")
        else:
            # Fond blanc : des coins noirs introduits par la rotation seraient
            # interprétés comme du contenu par le modèle vision.
            image = _rotate(image, -angle, border_value=255)
            steps.append("deskew")

    resized = downscale_to_max_edge(image, options.max_edge_px)
    if resized.shape != image.shape:
        steps.append("downscale")
    image = resized

    # Le contraste vient en dernier : CLAHE travaille par tuiles, son effet
    # dépend donc de la résolution finale de l'image.
    if options.contrast != "none":
        image = enhance_contrast(image, options.contrast)
        steps.append(options.contrast)

    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), image):
        raise ValueError(f"écriture impossible: {destination}")

    output_height, output_width = image.shape[:2]
    return PreprocessingResult(
        output_path=destination,
        deskew_angle=angle,
        steps_applied=tuple(steps),
        source_size=(source_width, source_height),
        output_size=(output_width, output_height),
    )
