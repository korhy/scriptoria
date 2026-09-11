"""Surface de l'API.

Les routes non implémentées doivent renvoyer 501, pas 404 : la différence compte.
404 laisse croire à une faute de frappe ; 501 dit « prévu, pas encore écrit ».
"""

import pytest
from fastapi.testclient import TestClient


def test_liste_documents_traverse_la_base(client: TestClient) -> None:
    """Prouve le chaînage HTTP → dépendance → session. Liste vide attendue."""
    response = client.get("/documents")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/documents", {"source_filename": "page-test.png"}),
        ("/search", {"query": "facture 2019", "top_k": 3}),
        ("/search/answer", {"query": "facture 2019", "top_k": 3}),
    ],
)
def test_routes_non_implementees_renvoient_501(
    client: TestClient, path: str, payload: dict
) -> None:
    response = client.post(path, json=payload)
    assert response.status_code == 501
    # Le message doit orienter vers le module à écrire, pas rester muet.
    assert response.json()["detail"]


def test_recherche_rejette_une_requete_vide(client: TestClient) -> None:
    """La validation Pydantic doit intervenir avant le 501."""
    response = client.post("/search", json={"query": "", "top_k": 3})
    assert response.status_code == 422
