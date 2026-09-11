"""La sonde `/health` doit dire la vérité.

Un health check qui renvoie « ok » en dur ne prouve rien. Ces tests vérifient
qu'une dépendance en panne se traduit bien par un 503 et un booléen faux.
"""

from collections.abc import Callable

from fastapi.testclient import TestClient


def test_live_ne_touche_aucune_dependance(client_factory: Callable[..., TestClient]) -> None:
    """La sonde du conteneur doit répondre même sans aucune dépendance disponible."""
    client = client_factory(db=False, es=False, ollama=False)

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_health_ok_quand_tout_repond(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] is True
    assert body["elasticsearch"] is True
    assert body["ollama"] is True


def test_health_signale_postgres_en_panne(client_factory: Callable[..., TestClient]) -> None:
    response = client_factory(db=False).get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["db"] is False
    # Les dépendances saines ne doivent pas être marquées en panne par ricochet.
    assert body["elasticsearch"] is True
    assert body["ollama"] is True


def test_health_signale_ollama_en_panne(client_factory: Callable[..., TestClient]) -> None:
    """Cas le plus probable en pratique : `ollama serve` n'a pas été lancé."""
    response = client_factory(ollama=False).get("/health")

    assert response.status_code == 503
    assert response.json()["ollama"] is False
