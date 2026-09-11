"""Vectorisation via Ollama (`POST /api/embed`).

Passer par Ollama plutôt que par sentence-transformers en conteneur est délibéré :
torch dans Docker sur macOS ne voit pas le GPU Metal et tournerait en CPU. Ollama,
natif sur l'hôte, y accède.

La dimension produite (`EMBEDDING_DIM`, 1024 pour bge-m3) doit rester alignée avec
le mapping `dense_vector` de l'index — un écart se traduit par un rejet d'ES à
l'indexation, pas par une dégradation silencieuse.
"""

import httpx


async def embed_texts(
    client: httpx.AsyncClient,
    texts: list[str],
    model: str,
) -> list[list[float]]:
    """Vectorise un lot de textes.

    Args:
        client: client HTTP pointant sur Ollama.
        texts: fragments à vectoriser.
        model: modèle d'embedding, p.ex. `bge-m3`.

    Returns:
        Un vecteur par texte, dans le même ordre.
    """
    raise NotImplementedError("Embeddings Ollama — à implémenter")
