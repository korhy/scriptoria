"""Disposition des fichiers sur disque.

L'enjeu central est de sécurité : le nom de fichier vient de l'utilisateur, et il
ne doit jamais influencer l'endroit où l'on écrit. Ces tests vérifient que le
chemin est dérivé de l'identifiant du document, pas du nom fourni.
"""

from pathlib import Path
from uuid import UUID

import pytest

from scriptoria.services.storage import (
    MAX_PAGE_BYTES,
    MAX_PAGES_PER_DOCUMENT,
    UnsupportedImageError,
    preprocessed_page_relpath,
    raw_page_relpath,
    remove_document_files,
    validate_image_suffix,
    write_page_bytes,
)

DOC_ID = UUID("11111111-2222-3333-4444-555555555555")


# --- Validation d'extension -------------------------------------------------


@pytest.mark.parametrize(
    "filename", ["scan.png", "SCAN.PNG", "page.jpg", "page.jpeg", "p.tif", "p.tiff", "p.webp"]
)
def test_les_formats_image_courants_sont_acceptes(filename: str) -> None:
    assert validate_image_suffix(filename).startswith(".")


@pytest.mark.parametrize("filename", ["doc.pdf", "script.sh", "archive.zip", "sans_extension", ""])
def test_les_formats_non_image_sont_refuses(filename: str) -> None:
    with pytest.raises(UnsupportedImageError):
        validate_image_suffix(filename)


def test_un_nom_absent_est_refuse() -> None:
    """Starlette laisse `filename` à None quand le client n'en fournit pas."""
    with pytest.raises(UnsupportedImageError):
        validate_image_suffix(None)


# --- Dérivation des chemins -------------------------------------------------


def test_le_chemin_brut_derive_de_l_identifiant_et_du_numero() -> None:
    assert raw_page_relpath(DOC_ID, 7, ".png") == Path("inbox") / str(DOC_ID) / "0007.png"


def test_le_numero_de_page_est_complete_pour_rester_trie() -> None:
    """Sans complétion, la page 10 se classerait avant la page 2."""
    chemins = [raw_page_relpath(DOC_ID, n, ".png").name for n in (2, 10)]

    assert sorted(chemins) == chemins


def test_le_chemin_pretraite_est_toujours_en_png() -> None:
    """Format sans perte : recompresser en JPEG ajouterait des artefacts au
    moment précis où l'on cherche à nettoyer l'image."""
    assert preprocessed_page_relpath(DOC_ID, 3).suffix == ".png"


def test_les_chemins_sont_relatifs_a_data_dir() -> None:
    """Le modèle stocke des chemins relatifs : déplacer DATA_DIR ne doit pas
    invalider la base."""
    assert not raw_page_relpath(DOC_ID, 1, ".png").is_absolute()
    assert not preprocessed_page_relpath(DOC_ID, 1).is_absolute()


@pytest.mark.parametrize(
    "hostile",
    [
        "../../../etc/passwd.png",
        "/etc/shadow.png",
        "..\\..\\windows\\system32.png",
        "....//....//evil.png",
    ],
)
def test_un_nom_de_fichier_hostile_n_influence_pas_le_chemin(hostile: str) -> None:
    """Sécurité : le nom fourni par l'utilisateur ne sert qu'à valider le format.

    Le chemin d'écriture est construit depuis l'identifiant du document et le
    numéro de page. La traversée de répertoire est donc impossible par
    construction, pas par assainissement — un assainissement se contourne.
    """
    suffix = validate_image_suffix(hostile)

    chemin = raw_page_relpath(DOC_ID, 1, suffix)

    assert ".." not in chemin.parts
    assert chemin == Path("inbox") / str(DOC_ID) / "0001.png"


# --- Écriture et nettoyage --------------------------------------------------


def test_l_ecriture_cree_les_dossiers_manquants(tmp_path: Path) -> None:
    destination = tmp_path / "inbox" / str(DOC_ID) / "0001.png"

    write_page_bytes(destination, b"contenu")

    assert destination.read_bytes() == b"contenu"


def test_le_nettoyage_ne_supprime_que_le_document_vise(tmp_path: Path) -> None:
    """Un import qui échoue à mi-parcours ne doit pas laisser de fichiers
    orphelins — ni emporter ceux des autres documents."""
    autre = UUID("99999999-8888-7777-6666-555555555555")
    write_page_bytes(tmp_path / raw_page_relpath(DOC_ID, 1, ".png"), b"a")
    write_page_bytes(tmp_path / raw_page_relpath(autre, 1, ".png"), b"b")

    remove_document_files(tmp_path, DOC_ID)

    assert not (tmp_path / "inbox" / str(DOC_ID)).exists()
    assert (tmp_path / raw_page_relpath(autre, 1, ".png")).exists()


def test_le_nettoyage_d_un_document_inexistant_ne_leve_pas(tmp_path: Path) -> None:
    """Appelé depuis un gestionnaire d'erreur : il ne doit pas masquer la panne
    d'origine en levant à son tour."""
    remove_document_files(tmp_path, DOC_ID)


# --- Limites ----------------------------------------------------------------


def test_les_limites_sont_explicites() -> None:
    """Des bornes existent pour éviter qu'un import remplisse le disque."""
    assert MAX_PAGES_PER_DOCUMENT > 0
    assert MAX_PAGE_BYTES > 0
