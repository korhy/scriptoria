"""Vignettes de la galerie.

Une page prétraitée pèse de l'ordre du mégaoctet ; la galerie en affiche des
dizaines. La vignette est réduite côté API, en JPEG, et jamais stockée : elle se
dérive de l'image en une fraction de seconde.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scriptoria.api.deps import get_db
from scriptoria.config import get_settings
from scriptoria.db.models import Page
from scriptoria.services.thumbnails import UnreadableImageError, render_thumbnail

JPEG_MAGIC = b"\xff\xd8"


def ecrire_image(chemin: Path, largeur: int = 400, hauteur: int = 800) -> Path:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(chemin), np.full((hauteur, largeur), 200, dtype=np.uint8))
    return chemin


def decoder(octets: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(octets, dtype=np.uint8), cv2.IMREAD_UNCHANGED)


# --- Service ---------------------------------------------------------------------


def test_la_vignette_est_un_jpeg_a_la_largeur_demandee_proportions_gardees(
    tmp_path: Path,
) -> None:
    source = ecrire_image(tmp_path / "page.png", largeur=400, hauteur=800)

    octets = render_thumbnail(source, width=100)

    assert octets.startswith(JPEG_MAGIC)
    assert decoder(octets).shape[:2] == (200, 100)


def test_une_image_plus_petite_que_la_vignette_n_est_pas_agrandie(tmp_path: Path) -> None:
    source = ecrire_image(tmp_path / "page.png", largeur=80, hauteur=120)

    assert decoder(render_thumbnail(source, width=240)).shape[:2] == (120, 80)


def test_une_image_illisible_leve_une_erreur_explicite(tmp_path: Path) -> None:
    corrompue = tmp_path / "page.png"
    corrompue.write_bytes(b"pas une image")

    with pytest.raises(UnreadableImageError):
        render_thumbnail(corrompue, width=100)


# --- Route -----------------------------------------------------------------------


class FakeResult:
    def __init__(self, page: Page | None) -> None:
        self._page = page

    def scalar_one_or_none(self) -> Page | None:
        return self._page


class FakeSession:
    def __init__(self, page: Page | None) -> None:
        self.page = page

    async def execute(self, statement: Any) -> FakeResult:
        return FakeResult(self.page)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.fixture
def contexte(app: FastAPI, tmp_path: Path) -> tuple[TestClient, Page]:
    document_id = uuid4()
    page = Page(
        id=uuid4(),
        document_id=document_id,
        page_number=1,
        raw_image_path=f"inbox/{document_id}/0001.png",
        preprocessed_image_path=f"images/{document_id}/0001.png",
    )
    ecrire_image(tmp_path / page.preprocessed_image_path)

    async def _db() -> AsyncIterator[FakeSession]:
        yield FakeSession(page)

    settings = get_settings().model_copy(update={"data_dir": tmp_path})
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, raise_server_exceptions=False), page


def url(page: Page, **params: object) -> str:
    requete = "&".join(f"{cle}={valeur}" for cle, valeur in params.items())
    return f"/documents/{page.document_id}/pages/{page.page_number}/thumbnail?{requete}"


def test_la_route_rend_une_vignette_jpeg(contexte: tuple[TestClient, Page]) -> None:
    client, page = contexte

    response = client.get(url(page, width=120))

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/jpeg"
    assert decoder(response.content).shape[1] == 120


@pytest.mark.parametrize("largeur", [10, 5000])
def test_une_largeur_hors_bornes_est_refusee(
    contexte: tuple[TestClient, Page], largeur: int
) -> None:
    """Une vignette de 5000 px n'en est plus une : c'est l'image entière, réencodée."""
    client, page = contexte

    assert client.get(url(page, width=largeur)).status_code == 422


def test_une_image_pretraitee_pas_encore_produite_donne_404(
    contexte: tuple[TestClient, Page],
) -> None:
    client, page = contexte
    page.preprocessed_image_path = None

    response = client.get(url(page, width=120))

    assert response.status_code == 404
    assert "prétraitement" in response.json()["detail"]


def test_la_variante_brute_est_disponible(
    contexte: tuple[TestClient, Page], tmp_path: Path
) -> None:
    client, page = contexte
    ecrire_image(tmp_path / page.raw_image_path, largeur=300, hauteur=300)

    response = client.get(url(page, width=150, variant="raw"))

    assert response.status_code == 200, response.text
    assert decoder(response.content).shape[:2] == (150, 150)


def test_une_image_corrompue_sur_le_disque_donne_404_explicite(
    contexte: tuple[TestClient, Page], tmp_path: Path
) -> None:
    client, page = contexte
    (tmp_path / page.preprocessed_image_path).write_bytes(b"corrompu")

    response = client.get(url(page, width=120))

    assert response.status_code == 404
    assert "illisible" in response.json()["detail"]
