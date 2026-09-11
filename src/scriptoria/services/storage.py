"""Disposition des fichiers de pages sur disque.

**Le nom de fichier fourni par l'utilisateur ne détermine jamais où l'on écrit.**
Il sert uniquement à valider le format, puis est conservé en base pour affichage.
Le chemin d'écriture est construit depuis l'identifiant du document et le numéro
de page. La traversée de répertoire (`../../etc/passwd`) est donc impossible par
construction — un assainissement de chaîne, lui, finit toujours par se contourner.

Disposition :

    <DATA_DIR>/inbox/<document_id>/0001.png    image brute, jamais modifiée
    <DATA_DIR>/images/<document_id>/0001.png   image prétraitée, reproductible

Les chemins stockés en base sont relatifs à `DATA_DIR` : déplacer le répertoire
de données ne doit pas invalider la base.
"""

import shutil
from pathlib import Path
from uuid import UUID

RAW_ROOT = "inbox"
PREPROCESSED_ROOT = "images"

ALLOWED_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"})

# Bornes de garde : sans elles, un seul import peut remplir le disque.
MAX_PAGES_PER_DOCUMENT = 200
MAX_PAGE_BYTES = 25 * 1024 * 1024


class UnsupportedImageError(ValueError):
    """Le fichier fourni n'est pas dans un format image pris en charge."""


def validate_image_suffix(filename: str | None) -> str:
    """Valide l'extension et la retourne en minuscules.

    Le PDF est délibérément absent : il faudrait en rendre chaque page en image,
    ce qui suppose une dépendance supplémentaire. Mieux vaut refuser clairement
    que d'accepter un fichier qu'on ne saura pas traiter.

    Raises:
        UnsupportedImageError: nom absent, ou extension non reconnue.
    """
    if not filename:
        raise UnsupportedImageError("nom de fichier absent")

    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        acceptes = ", ".join(sorted(ALLOWED_IMAGE_SUFFIXES))
        rejete = suffix or f"{filename!r} (sans extension)"
        raise UnsupportedImageError(f"format non pris en charge: {rejete}. Acceptés: {acceptes}.")
    return suffix


def raw_page_relpath(document_id: UUID, page_number: int, suffix: str) -> Path:
    """Chemin de l'image brute, relatif à `DATA_DIR`.

    Le numéro est complété à quatre chiffres pour que l'ordre alphabétique des
    fichiers corresponde à l'ordre des pages.
    """
    return Path(RAW_ROOT) / str(document_id) / f"{page_number:04d}{suffix}"


def preprocessed_page_relpath(document_id: UUID, page_number: int) -> Path:
    """Chemin de l'image prétraitée, relatif à `DATA_DIR`.

    Toujours en PNG : recompresser en JPEG ajouterait des artefacts au moment
    précis où l'on cherche à nettoyer l'image.
    """
    return Path(PREPROCESSED_ROOT) / str(document_id) / f"{page_number:04d}.png"


def write_page_bytes(destination: Path, payload: bytes) -> None:
    """Écrit une image, en créant l'arborescence au besoin.

    Fonction bloquante : à appeler depuis un thread quand on est en contexte async.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)


def remove_document_files(data_dir: Path, document_id: UUID) -> None:
    """Supprime les fichiers d'un document, brut et prétraité.

    Appelée depuis un gestionnaire d'erreur pour éviter les fichiers orphelins
    quand un import échoue à mi-parcours. Ne lève jamais : elle masquerait
    l'erreur qu'on est en train de traiter.
    """
    for root in (RAW_ROOT, PREPROCESSED_ROOT):
        shutil.rmtree(data_dir / root / str(document_id), ignore_errors=True)
