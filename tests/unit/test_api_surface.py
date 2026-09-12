"""Surface de l'API.

Ce fichier a changé de rôle avec la phase 5 : il vérifiait que les routes non
écrites renvoyaient 501 plutôt que 404 — 404 laissant croire à une faute de
frappe, 501 disant « prévu, pas encore écrit ». **Plus aucune route ne renvoie
501** : le pipeline est complet de l'import à la réponse générée.

Ce qui reste utile est le contraire : constater que la surface annoncée existe,
et qu'aucune route ne prétend encore être à écrire.
"""

from fastapi.testclient import TestClient

ROUTES_ATTENDUES = [
    ("/documents", "post"),
    ("/documents", "get"),
    ("/documents/{document_id}", "get"),
    ("/documents/{document_id}/pages", "get"),
    ("/documents/{document_id}/pages/{page_number}/image", "get"),
    ("/documents/{document_id}/transcribe", "post"),
    ("/pages/{page_id}", "get"),
    ("/pages/{page_id}/corrections", "post"),
    ("/search", "post"),
    ("/search/answer", "post"),
    ("/health", "get"),
]


def test_liste_documents_traverse_la_base(client: TestClient) -> None:
    """Prouve le chaînage HTTP → dépendance → session. Liste vide attendue."""
    response = client.get("/documents")
    assert response.status_code == 200
    assert response.json() == []


def test_la_surface_annoncee_existe(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    for chemin, methode in ROUTES_ATTENDUES:
        assert methode in schema["paths"].get(chemin, {}), f"{methode.upper()} {chemin} absente"


def test_plus_aucune_route_ne_se_declare_a_ecrire(client: TestClient) -> None:
    """Garde-fou contre une régression : un 501 signalerait un retour en arrière."""
    schema = client.get("/openapi.json").json()

    non_implementees = [
        (chemin, methode)
        for chemin, operations in schema["paths"].items()
        for methode, operation in operations.items()
        if "501" in operation.get("responses", {})
    ]

    assert non_implementees == []


def test_recherche_rejette_une_requete_vide(client: TestClient) -> None:
    """La validation Pydantic doit intervenir avant tout appel de modèle."""
    response = client.post("/search", json={"query": "", "top_k": 3})
    assert response.status_code == 422
