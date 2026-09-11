"""Import de documents — rejets.

Le chemin nominal traverse la base, le disque et Redis : il est vérifié par
`tests/integration/test_import_pipeline.py` sur la vraie stack. Ici on couvre
les rejets, qui doivent intervenir **avant** toute écriture.
"""

import pytest
from fastapi.testclient import TestClient

from scriptoria.services.storage import MAX_PAGES_PER_DOCUMENT

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x00\x00\x00\x00:~\x9bU\x00\x00\x00\nIDAT\x08\x1dc`\x00\x00\x00"
    b"\x02\x00\x01\xe2!\xbc3\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_un_import_sans_fichier_est_refuse(client: TestClient) -> None:
    response = client.post("/documents", files=[])

    assert response.status_code == 422


def test_un_format_non_image_est_refuse(client: TestClient) -> None:
    """Un PDF exigerait un rendu page par page : hors périmètre, et le dire."""
    response = client.post("/documents", files=[("files", ("facture.pdf", b"%PDF-1.4", ""))])

    assert response.status_code == 400
    assert ".pdf" in response.json()["detail"].lower()


def test_un_nom_de_fichier_hostile_est_refuse_faute_d_extension(client: TestClient) -> None:
    """`../../etc/passwd` n'a pas d'extension image : rejeté avant toute écriture.

    Même s'il en avait une, le chemin d'écriture ne dérive jamais du nom fourni
    — voir tests/unit/test_storage.py.
    """
    response = client.post("/documents", files=[("files", ("../../etc/passwd", PNG, ""))])

    assert response.status_code == 400


def test_trop_de_pages_est_refuse(client: TestClient) -> None:
    trop = MAX_PAGES_PER_DOCUMENT + 1
    fichiers = [("files", (f"p{n}.png", PNG, "image/png")) for n in range(trop)]

    response = client.post("/documents", files=fichiers)

    assert response.status_code == 413


def test_une_page_trop_lourde_est_refusee(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Borne le disque : sans elle, un seul import peut le remplir."""
    monkeypatch.setattr("scriptoria.api.routers.documents.MAX_PAGE_BYTES", 10)

    response = client.post("/documents", files=[("files", ("grande.png", PNG, "image/png"))])

    assert response.status_code == 413
