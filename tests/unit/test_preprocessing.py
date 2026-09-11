"""Prétraitement image.

Les primitives opèrent sur des tableaux numpy, sans I/O : c'est ce qui permet de
les tester sans fichier ni fixture binaire committée. Seule `preprocess_page`
touche au disque, et son invariant central — ne jamais modifier la source — est
vérifié par empreinte.

Le deskew est testé par construction : on part d'une image droite, on la tourne
d'un angle connu, et on vérifie que l'estimation le retrouve.
"""

import hashlib
from pathlib import Path

import cv2
import numpy as np
import pytest

from scriptoria.services.preprocessing import (
    PreprocessingOptions,
    denoise,
    downscale_to_max_edge,
    enhance_contrast,
    estimate_skew_angle,
    preprocess_page,
    to_grayscale,
)


def make_text_page(width: int = 800, height: int = 1000) -> np.ndarray:
    """Page synthétique : des barres sombres horizontales figurant des lignes."""
    page = np.full((height, width), 255, dtype=np.uint8)
    for top in range(80, height - 80, 40):
        page[top : top + 12, 60 : width - 60] = 40
    return page


def make_sparse_text_page(width: int = 800, height: int = 1000) -> np.ndarray:
    """Page à faible densité d'encre, proche d'un document réel.

    `make_text_page` produit des barres épaisses qui donnent un signal de
    projection bien plus franc que du vrai texte. Une page A4 réelle ne porte
    que ~0,4 % de pixels d'encre : c'est ce régime-là qu'il faut tester, sinon
    les garde-fous se calibrent sur un cas irréaliste.
    """
    page = np.full((height, width), 255, dtype=np.uint8)
    for top in range(100, height - 100, 60):
        page[top : top + 2, 120 : width - 120] = 30
    return page


def rotate(image: np.ndarray, angle: float) -> np.ndarray:
    """Tourne autour du centre, fond blanc — comme une page scannée de travers."""
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    return cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_LINEAR, borderValue=255)


# --- Niveaux de gris --------------------------------------------------------


def test_une_image_couleur_devient_mono_canal() -> None:
    couleur = np.zeros((10, 20, 3), dtype=np.uint8)

    assert to_grayscale(couleur).shape == (10, 20)


def test_une_image_deja_grise_traverse_sans_changement() -> None:
    grise = make_text_page(40, 30)

    assert np.array_equal(to_grayscale(grise), grise)


# --- Redimensionnement ------------------------------------------------------


def test_le_plus_grand_cote_est_ramene_a_la_cible() -> None:
    image = np.zeros((2000, 1000), dtype=np.uint8)

    reduite = downscale_to_max_edge(image, 800)

    assert max(reduite.shape) == 800


def test_le_ratio_d_aspect_est_preserve() -> None:
    image = np.zeros((2000, 1000), dtype=np.uint8)

    hauteur, largeur = downscale_to_max_edge(image, 800).shape

    assert hauteur / largeur == pytest.approx(2.0, abs=0.01)


def test_une_image_deja_petite_n_est_jamais_agrandie() -> None:
    """Agrandir n'ajoute aucune information et gonfle le coût d'encodage OCR."""
    image = np.zeros((300, 200), dtype=np.uint8)

    assert downscale_to_max_edge(image, 1600).shape == (300, 200)


# --- Débruitage -------------------------------------------------------------


def test_le_debruitage_rapproche_l_image_de_l_originale() -> None:
    propre = make_text_page(400, 500)
    rng = np.random.default_rng(seed=42)
    bruitee = np.clip(propre + rng.normal(0, 25, propre.shape), 0, 255).astype(np.uint8)

    ecart_avant = np.abs(bruitee.astype(int) - propre.astype(int)).mean()
    ecart_apres = np.abs(denoise(bruitee).astype(int) - propre.astype(int)).mean()

    assert ecart_apres < ecart_avant


def test_le_debruitage_preserve_les_traits() -> None:
    """Un débruitage qui efface le texte serait pire que pas de débruitage."""
    page = make_text_page(400, 500)

    debruitee = denoise(page)

    # Les pixels sombres (les lignes) doivent rester sombres.
    assert debruitee[page < 128].mean() < 100


# --- Contraste --------------------------------------------------------------


def test_clahe_augmente_le_contraste_d_une_image_terne() -> None:
    terne = np.random.default_rng(seed=1).integers(100, 120, (200, 200), dtype=np.uint8)

    rehaussee = enhance_contrast(terne, "clahe")

    assert rehaussee.std() > terne.std()


def test_le_mode_none_ne_touche_a_rien() -> None:
    page = make_text_page(100, 100)

    assert np.array_equal(enhance_contrast(page, "none"), page)


def test_le_seuillage_adaptatif_ne_produit_que_du_noir_et_du_blanc() -> None:
    """Mode disponible mais non recommandé pour un LLM vision — voir le module."""
    page = make_text_page(200, 200)

    binaire = enhance_contrast(page, "adaptive_threshold")

    assert set(np.unique(binaire)).issubset({0, 255})


# --- Estimation d'inclinaison -----------------------------------------------


@pytest.mark.parametrize("angle", [-7.0, -3.0, -0.5, 0.0, 0.5, 3.0, 7.0])
def test_l_angle_connu_est_retrouve(angle: float) -> None:
    inclinee = rotate(make_text_page(), angle)

    estime = estimate_skew_angle(inclinee)

    assert estime == pytest.approx(angle, abs=0.5)


@pytest.mark.parametrize("angle", [-5.0, -2.7, 2.7, 5.0])
def test_l_angle_est_retrouve_sur_une_page_peu_dense(angle: float) -> None:
    """Régression : un garde-fou d'encre trop haut rejetait les pages réelles.

    Détecté en faisant tourner le CLI sur une vraie page A4 (0,38 % d'encre)
    alors que le plancher était à 0,5 % — le redressement était silencieusement
    ignoré alors que l'estimation, elle, était juste à 0,05° près.
    """
    inclinee = rotate(make_sparse_text_page(), angle)

    assert estimate_skew_angle(inclinee) == pytest.approx(angle, abs=0.5)


def test_une_page_vide_ne_declenche_aucune_rotation() -> None:
    """Sans encre, il n'y a rien à aligner : tourner serait du hasard."""
    vide = np.full((500, 400), 255, dtype=np.uint8)

    assert estimate_skew_angle(vide) == 0.0


def test_un_scan_sature_ne_declenche_aucune_rotation() -> None:
    """Autre cas dégénéré : une image entièrement sombre n'a pas de lignes."""
    sature = np.full((500, 400), 10, dtype=np.uint8)

    assert estimate_skew_angle(sature) == 0.0


# --- Orchestration ----------------------------------------------------------


@pytest.fixture
def page_sur_disque(tmp_path: Path) -> Path:
    chemin = tmp_path / "source.png"
    cv2.imwrite(str(chemin), rotate(make_text_page(), 3.0))
    return chemin


def test_l_image_source_n_est_jamais_modifiee(page_sur_disque: Path, tmp_path: Path) -> None:
    """Invariant central : on doit toujours pouvoir rejouer depuis l'original."""
    avant = hashlib.sha256(page_sur_disque.read_bytes()).hexdigest()

    preprocess_page(page_sur_disque, tmp_path / "out.png")

    assert hashlib.sha256(page_sur_disque.read_bytes()).hexdigest() == avant


def test_le_fichier_de_sortie_est_ecrit(page_sur_disque: Path, tmp_path: Path) -> None:
    destination = tmp_path / "sous" / "dossier" / "out.png"

    resultat = preprocess_page(page_sur_disque, destination)

    assert destination.exists()
    assert resultat.output_path == destination


def test_l_inclinaison_est_corrigee_et_consignee(page_sur_disque: Path, tmp_path: Path) -> None:
    resultat = preprocess_page(page_sur_disque, tmp_path / "out.png")

    assert resultat.deskew_angle == pytest.approx(3.0, abs=0.5)
    redressee = cv2.imread(str(tmp_path / "out.png"), cv2.IMREAD_GRAYSCALE)
    assert estimate_skew_angle(redressee) == pytest.approx(0.0, abs=0.5)


def test_desactiver_le_deskew_laisse_l_angle_a_zero(page_sur_disque: Path, tmp_path: Path) -> None:
    options = PreprocessingOptions(deskew=False)

    resultat = preprocess_page(page_sur_disque, tmp_path / "out.png", options)

    assert resultat.deskew_angle == 0.0
    assert "deskew" not in resultat.steps_applied


def test_les_etapes_appliquees_sont_tracees(page_sur_disque: Path, tmp_path: Path) -> None:
    """`steps_applied` sert à diagnostiquer une sortie OCR médiocre."""
    resultat = preprocess_page(page_sur_disque, tmp_path / "out.png")

    assert "grayscale" in resultat.steps_applied
    assert "denoise" in resultat.steps_applied
    assert "deskew" in resultat.steps_applied
    assert "clahe" in resultat.steps_applied


def test_la_page_est_redimensionnee_selon_les_options(
    page_sur_disque: Path, tmp_path: Path
) -> None:
    """La résolution d'entrée est le principal levier du coût OCR (37s/57s mesurés)."""
    options = PreprocessingOptions(max_edge_px=500)

    resultat = preprocess_page(page_sur_disque, tmp_path / "out.png", options)

    assert max(resultat.output_size) == 500
    assert resultat.source_size == (800, 1000)
