"""Tests d'intégration : nécessitent la stack démarrée (`make up`).

Marqués `integration` — exclus d'une exécution rapide :
    pytest -m "not integration"
"""

import os

import httpx
import pytest

API_BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")

pytestmark = pytest.mark.integration


def test_postgres_repond_reellement() -> None:
    response = httpx.get(f"{API_BASE_URL}/health", timeout=30.0)
    assert response.json()["db"] is True


def test_elasticsearch_repond_reellement() -> None:
    response = httpx.get(f"{API_BASE_URL}/health", timeout=30.0)
    assert response.json()["elasticsearch"] is True


@pytest.mark.ollama
def test_ollama_repond_reellement() -> None:
    """Échoue si `ollama serve` n'est pas lancé sur l'hôte — c'est voulu."""
    response = httpx.get(f"{API_BASE_URL}/health", timeout=30.0)
    assert response.json()["ollama"] is True
