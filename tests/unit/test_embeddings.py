"""Vectorisation via Ollama.

Le transport est simulé : aucun test ne sort de la machine. Ce qui est vérifié
ici, c'est la forme de la requête et le **refus de toute sortie douteuse** — un
vecteur de mauvaise dimension serait rejeté par Elasticsearch bien plus tard,
avec un message sans rapport avec la cause.
"""

import json
from collections.abc import Callable

import httpx
import pytest

from scriptoria.services.embeddings import EmbeddingError, embed_texts

MODELE = "bge-m3"


def client_simule(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ollama-simule"
    )


def capture(embeddings: list[list[float]]) -> tuple[Callable, list[dict]]:
    corps: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        corps.append(json.loads(request.read()))
        return httpx.Response(200, json={"embeddings": embeddings})

    return handler, corps


async def test_les_textes_partent_en_un_seul_appel() -> None:
    """Un appel par fragment multiplierait les allers-retours pour rien."""
    handler, corps = capture([[0.1, 0.2], [0.3, 0.4]])

    async with client_simule(handler) as client:
        await embed_texts(client, ["premier", "second"], model=MODELE)

    assert len(corps) == 1
    assert corps[0]["model"] == MODELE
    assert corps[0]["input"] == ["premier", "second"]


async def test_les_vecteurs_reviennent_dans_l_ordre_des_textes() -> None:
    handler, _ = capture([[0.1, 0.2], [0.3, 0.4]])

    async with client_simule(handler) as client:
        vecteurs = await embed_texts(client, ["premier", "second"], model=MODELE)

    assert vecteurs == [[0.1, 0.2], [0.3, 0.4]]


async def test_aucun_texte_n_appelle_pas_le_modele() -> None:
    appels: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        appels.append(request)
        return httpx.Response(200, json={"embeddings": []})

    async with client_simule(handler) as client:
        assert await embed_texts(client, [], model=MODELE) == []

    assert appels == []


async def test_un_nombre_de_vecteurs_inattendu_est_une_erreur() -> None:
    """Deux textes, un vecteur : l'appariement texte ↔ vecteur serait faux."""
    handler, _ = capture([[0.1, 0.2]])

    async with client_simule(handler) as client:
        with pytest.raises(EmbeddingError) as erreur:
            await embed_texts(client, ["premier", "second"], model=MODELE)

    assert "2" in str(erreur.value)


async def test_des_dimensions_incoherentes_sont_une_erreur() -> None:
    handler, _ = capture([[0.1, 0.2], [0.3]])

    async with client_simule(handler) as client:
        with pytest.raises(EmbeddingError):
            await embed_texts(client, ["premier", "second"], model=MODELE)


async def test_un_vecteur_vide_est_une_erreur() -> None:
    handler, _ = capture([[]])

    async with client_simule(handler) as client:
        with pytest.raises(EmbeddingError):
            await embed_texts(client, ["premier"], model=MODELE)


async def test_une_erreur_http_est_signalee_avec_le_modele() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    async with client_simule(handler) as client:
        with pytest.raises(EmbeddingError) as erreur:
            await embed_texts(client, ["texte"], model="absent")

    assert "absent" in str(erreur.value)


async def test_ollama_injoignable_est_signale_avec_son_adresse() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with client_simule(handler) as client:
        with pytest.raises(EmbeddingError) as erreur:
            await embed_texts(client, ["texte"], model=MODELE)

    assert "ollama-simule" in str(erreur.value)


async def test_une_reponse_sans_embeddings_est_une_erreur() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": MODELE})

    async with client_simule(handler) as client:
        with pytest.raises(EmbeddingError):
            await embed_texts(client, ["texte"], model=MODELE)
