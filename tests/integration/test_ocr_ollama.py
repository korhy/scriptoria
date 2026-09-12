"""OCR réel : une vraie page, un vrai modèle vision.

Le seul test qui prouve que la requête construite dans `services/ocr.py` est
celle qu'Ollama attend — les tests unitaires simulent le transport, ils ne
peuvent pas attraper un champ mal nommé.

Long par nature : ~57 s par page sur la machine cible, modèle déjà chargé, plus
~9 s de chargement à froid. D'où les marqueurs `ollama` et `slow`.

    uv run pytest -m ollama
"""

import os
from pathlib import Path

import httpx
import pytest

from scriptoria.config import get_settings
from scriptoria.services.ocr import transcribe_page

pytestmark = [pytest.mark.integration, pytest.mark.ollama, pytest.mark.slow]

FIXTURE = Path(__file__).parent.parent / "fixtures" / "page-test.png"

# Hors conteneur, Ollama écoute sur localhost ; dedans, sur host.docker.internal.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", get_settings().ollama_base_url)


@pytest.fixture
async def ollama() -> httpx.AsyncClient:
    settings = get_settings()
    async with httpx.AsyncClient(
        base_url=OLLAMA_BASE_URL, timeout=settings.ollama_timeout_seconds
    ) as client:
        try:
            await client.get("/api/tags")
        except httpx.HTTPError as exc:
            pytest.skip(f"Ollama injoignable sur {OLLAMA_BASE_URL}: {exc}")
        yield client


async def test_une_page_reelle_est_transcrite_en_markdown(ollama: httpx.AsyncClient) -> None:
    settings = get_settings()

    resultat = await transcribe_page(ollama, FIXTURE, model=settings.ollama_vision_model)

    assert resultat.content_markdown.strip()
    assert resultat.model_name == settings.ollama_vision_model
    # Sert de garde-fou sur le coût : une transcription instantanée signalerait
    # une réponse vide acceptée par erreur, pas un modèle miraculeux.
    assert resultat.duration_seconds > 0.5


async def test_la_structure_de_tableau_survit_a_la_transcription(
    ollama: httpx.AsyncClient,
) -> None:
    """La fixture porte un tableau : c'est le balisage dont dépendra le chunking."""
    settings = get_settings()

    resultat = await transcribe_page(ollama, FIXTURE, model=settings.ollama_vision_model)

    assert "|" in resultat.content_markdown, resultat.content_markdown[:500]
