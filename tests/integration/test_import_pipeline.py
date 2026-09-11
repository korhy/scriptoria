"""Flux d'import complet : API → Postgres → disque → Redis → worker.

Nécessite la stack démarrée (`make up`). C'est le seul test qui prouve que les
morceaux sont réellement branchés entre eux : les tests unitaires remplacent
la base, le disque et la file.
"""

import io
import os
import time

import cv2
import httpx
import numpy as np
import pytest

API_BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")

pytestmark = pytest.mark.integration


def page_png(angle: float = 2.5) -> bytes:
    """Page de texte synthétique, volontairement penchée."""
    page = np.full((1000, 800), 255, dtype=np.uint8)
    for top in range(100, 900, 60):
        page[top : top + 3, 120:680] = 30
    matrix = cv2.getRotationMatrix2D((400, 500), angle, 1.0)
    incline = cv2.warpAffine(page, matrix, (800, 1000), borderValue=255)
    return cv2.imencode(".png", incline)[1].tobytes()


def attendre_statut(document_id: str, attendu: str, timeout: float = 120.0) -> dict:
    """Scrute le document jusqu'à l'état voulu. Le worker travaille en arrière-plan."""
    echeance = time.monotonic() + timeout
    dernier: dict = {}
    while time.monotonic() < echeance:
        dernier = httpx.get(f"{API_BASE_URL}/documents/{document_id}", timeout=10.0).json()
        if dernier["status"] == attendu:
            return dernier
        if dernier["status"] == "failed":
            pytest.fail(f"le document est passé en échec: {dernier}")
        time.sleep(1.0)
    pytest.fail(f"statut '{attendu}' jamais atteint en {timeout}s, dernier: {dernier}")
    raise AssertionError  # inatteignable, pour le typage


def test_un_import_traverse_le_pipeline_jusqu_au_pretraitement() -> None:
    fichiers = [
        ("files", ("page1.png", io.BytesIO(page_png(2.5)), "image/png")),
        ("files", ("page2.png", io.BytesIO(page_png(-3.0)), "image/png")),
    ]

    creation = httpx.post(f"{API_BASE_URL}/documents", files=fichiers, timeout=60.0)

    assert creation.status_code == 201, creation.text
    document = creation.json()
    assert document["page_count"] == 2
    assert document["source_filename"] == "page1.png"

    # Le worker reprend la main : l'API n'a fait qu'enfiler.
    final = attendre_statut(document["id"], "preprocessed")
    assert final["status"] == "preprocessed"


def test_les_pages_pretraitees_sont_ecrites_sur_disque() -> None:
    fichiers = [("files", ("scan.png", io.BytesIO(page_png(4.0)), "image/png"))]

    document = httpx.post(f"{API_BASE_URL}/documents", files=fichiers, timeout=60.0).json()
    attendre_statut(document["id"], "preprocessed")

    pages = httpx.get(f"{API_BASE_URL}/documents/{document['id']}/pages", timeout=10.0).json()

    assert len(pages) == 1
    page = pages[0]
    assert page["raw_image_path"].startswith("inbox/")
    # C'est ce champ qui prouve que le worker a réellement travaillé.
    assert page["preprocessed_image_path"], "le worker n'a pas renseigné l'image prétraitée"
    assert page["preprocessed_image_path"].startswith("images/")


def test_l_image_source_survit_au_pretraitement() -> None:
    """Invariant du projet : on doit toujours pouvoir rejouer depuis l'original."""
    contenu = page_png(1.5)
    fichiers = [("files", ("scan.png", io.BytesIO(contenu), "image/png"))]

    document = httpx.post(f"{API_BASE_URL}/documents", files=fichiers, timeout=60.0).json()
    attendre_statut(document["id"], "preprocessed")

    pages = httpx.get(f"{API_BASE_URL}/documents/{document['id']}/pages", timeout=10.0).json()
    brute = httpx.get(
        f"{API_BASE_URL}/documents/{document['id']}/pages/{pages[0]['page_number']}/image",
        params={"variant": "raw"},
        timeout=30.0,
    )

    assert brute.status_code == 200
    assert brute.content == contenu
