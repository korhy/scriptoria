"""Retranscription d'une image de page en Markdown, via LLM vision local.

Passe par l'API HTTP d'Ollama (`POST /api/generate` avec le champ `images`).
Aucun SDK propriétaire, aucun appel réseau sortant : Ollama tourne sur l'hôte.

Contrainte mémoire : la machine cible a 16 Go unifiés. Ne jamais charger le
modèle vision et le modèle de génération en parallèle.
"""

from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True)
class OcrResult:
    """Sortie d'un passage OCR."""

    content_markdown: str
    model_name: str
    # Durée du passage : seule mesure fiable du coût réel sur cette machine.
    duration_seconds: float


async def transcribe_page(
    client: httpx.AsyncClient,
    image_path: Path,
    model: str,
    prompt: str | None = None,
) -> OcrResult:
    """Retranscrit une image de page en Markdown structuré.

    Args:
        client: client HTTP pointant sur Ollama (base_url déjà configurée).
        image_path: image à transcrire, de préférence déjà prétraitée.
        model: modèle vision Ollama, p.ex. `qwen2.5vl:7b`.
        prompt: consigne de transcription ; une consigne par défaut si absente.
    """
    raise NotImplementedError("Appel vision Ollama — à implémenter")
