"""Vectorisation via Ollama (`POST /api/embed`).

Passer par Ollama plutôt que par sentence-transformers en conteneur est délibéré :
torch dans Docker sur macOS ne voit pas le GPU Metal et tournerait en CPU. Ollama,
natif sur l'hôte, y accède.

La dimension produite (`EMBEDDING_DIM`, 1024 pour bge-m3) doit rester alignée avec
le mapping `dense_vector` de l'index — un écart se traduit par un rejet d'ES à
l'indexation, pas par une dégradation silencieuse. Les vérifications ci-dessous
le font échouer **ici**, où le message peut encore nommer la cause.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

EMBED_PATH = "/api/embed"


class EmbeddingError(RuntimeError):
    """La vectorisation n'a pas abouti, ou sa sortie est inexploitable."""


def _validate(vectors: list[list[float]], expected_count: int, model: str) -> None:
    """Refuse toute sortie qu'on ne saurait apparier ou indexer.

    Un décompte qui ne correspond pas aux textes soumis est le pire cas : il
    associerait silencieusement chaque fragment au vecteur d'un autre.
    """
    if len(vectors) != expected_count:
        raise EmbeddingError(
            f"{model} a renvoyé {len(vectors)} vecteur(s) pour {expected_count} texte(s) : "
            "l'appariement texte ↔ vecteur serait faux."
        )

    dimensions = {len(vector) for vector in vectors}
    if dimensions == {0} or 0 in dimensions:
        raise EmbeddingError(f"{model} a renvoyé un vecteur vide.")
    if len(dimensions) > 1:
        raise EmbeddingError(
            f"{model} a renvoyé des dimensions incohérentes : {sorted(dimensions)}."
        )


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

    Raises:
        EmbeddingError: Ollama en erreur, ou sortie inexploitable (décompte,
            dimension).
    """
    if not texts:
        return []

    try:
        response = await client.post(EMBED_PATH, json={"model": model, "input": texts})
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise EmbeddingError(
            f"Ollama a répondu {exc.response.status_code} pour le modèle {model} : "
            f"{exc.response.text[:200]}"
        ) from exc
    except httpx.HTTPError as exc:
        raise EmbeddingError(
            f"Ollama injoignable sur {client.base_url} — Ollama tourne-t-il sur l'hôte ? ({exc})"
        ) from exc

    vectors = response.json().get("embeddings")
    if vectors is None:
        raise EmbeddingError(f"réponse sans embeddings pour le modèle {model}.")

    _validate(vectors, len(texts), model)
    logger.info("%s fragment(s) vectorisé(s) en %s dimensions", len(vectors), len(vectors[0]))
    return vectors
