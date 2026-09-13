"""Interrogation du RAG : recherche hybride et réponse générée.

Les services d'embedding et de génération sont traversés pour de vrai — seul le
transport HTTP vers Ollama est simulé. Ce qui est vérifié ici, c'est
l'enchaînement (vectoriser la question, chercher, éventuellement rédiger) et le
**refus de faire passer une panne pour une absence de résultat** : Ollama éteint
ou index absent ne doivent pas se traduire par « aucun document ne correspond ».
"""

import json
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
from elasticsearch import NotFoundError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scriptoria.api.deps import get_es, get_ollama
from scriptoria.config import Settings, get_settings

DOCUMENT_ID = str(uuid4())
REPONSE_GENEREE = "L'encre coûte 28,90 € l'unité [1]."


class FakeEs:
    def __init__(self, hits: list[dict] | None = None, absent: bool = False) -> None:
        self.hits = hits if hits is not None else []
        self.absent = absent
        self.calls: list[dict] = []

    async def search(self, **kwargs: Any) -> dict:
        if self.absent:
            raise NotFoundError("index_not_found_exception", {}, {})
        self.calls.append(kwargs)
        return {"hits": {"hits": self.hits}}


def hit(chunk_id: str, page: int = 1, content: str = "| Encre | 6 | 28,90 |") -> dict:
    return {
        "_id": chunk_id,
        "_score": 1.0,
        "_source": {"document_id": DOCUMENT_ID, "page_number": page, "content": content},
    }


def ollama_simule(
    *, embeddings: list[list[float]] | None = None, generation: str = REPONSE_GENEREE
) -> tuple[httpx.AsyncClient, list[str]]:
    """Client Ollama simulé, et la liste des endpoints qu'il a reçus."""
    vus: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vus.append(request.url.path)
        if request.url.path == "/api/embed":
            if embeddings is None:
                return httpx.Response(500, text="model runner has crashed")
            return httpx.Response(200, json={"embeddings": embeddings})
        return httpx.Response(200, json={"response": generation})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ollama-simule"
    )
    return client, vus


@pytest.fixture
def contexte(app: FastAPI) -> Iterator[dict[str, Any]]:
    es = FakeEs(hits=[hit(f"{DOCUMENT_ID}:1")])
    ollama, endpoints = ollama_simule(embeddings=[[0.1] * 1024])

    app.dependency_overrides[get_es] = lambda: es
    app.dependency_overrides[get_ollama] = lambda: ollama
    yield {
        "client": TestClient(app, raise_server_exceptions=False),
        "es": es,
        "endpoints": endpoints,
        "app": app,
    }


# --- Recherche --------------------------------------------------------------


def test_la_question_est_vectorisee_puis_cherchee(contexte: dict[str, Any]) -> None:
    """Deux requêtes ES, jamais un retriever RRF : la licence basic le refuse."""
    response = contexte["client"].post("/search", json={"query": "encre", "top_k": 3})

    assert response.status_code == 200
    assert contexte["endpoints"] == ["/api/embed"]
    assert len(contexte["es"].calls) == 2


def test_chaque_resultat_porte_sa_page_et_son_document(contexte: dict[str, Any]) -> None:
    corps = contexte["client"].post("/search", json={"query": "encre", "top_k": 3}).json()

    assert len(corps["hits"]) == 1
    trouve = corps["hits"][0]
    assert trouve["document_id"] == DOCUMENT_ID
    assert trouve["page_number"] == 1
    assert "28,90" in trouve["content"]
    assert trouve["score"] > 0


def test_une_recherche_sans_resultat_rend_une_liste_vide(app: FastAPI) -> None:
    ollama, _ = ollama_simule(embeddings=[[0.1] * 1024])
    app.dependency_overrides[get_es] = lambda: FakeEs(hits=[])
    app.dependency_overrides[get_ollama] = lambda: ollama
    client = TestClient(app, raise_server_exceptions=False)

    corps = client.post("/search", json={"query": "inconnu", "top_k": 3}).json()

    assert corps["hits"] == []


def test_une_requete_vide_est_refusee_avant_tout_appel(contexte: dict[str, Any]) -> None:
    response = contexte["client"].post("/search", json={"query": "", "top_k": 3})

    assert response.status_code == 422
    assert contexte["endpoints"] == []


# --- Réponse générée --------------------------------------------------------


def test_la_reponse_est_generee_et_accompagnee_de_ses_sources(
    contexte: dict[str, Any],
) -> None:
    """Une réponse sans sources est invérifiable : l'OCR peut s'être trompé."""
    corps = contexte["client"].post("/search/answer", json={"query": "Combien ?"}).json()

    assert corps["answer"] == REPONSE_GENEREE
    assert len(corps["sources"]) == 1
    assert corps["sources"][0]["page_number"] == 1
    assert contexte["endpoints"] == ["/api/embed", "/api/generate"]


def test_sans_passage_aucun_modele_de_generation_n_est_charge(app: FastAPI) -> None:
    """Sur 16 Go, charger mistral pour ne rien avoir à dire coûte des secondes."""
    ollama, endpoints = ollama_simule(embeddings=[[0.1] * 1024])
    app.dependency_overrides[get_es] = lambda: FakeEs(hits=[])
    app.dependency_overrides[get_ollama] = lambda: ollama
    client = TestClient(app, raise_server_exceptions=False)

    corps = client.post("/search/answer", json={"query": "inconnu"}).json()

    assert corps["sources"] == []
    assert corps["answer"]
    assert endpoints == ["/api/embed"], "la génération a été appelée pour rien"


def test_seules_les_pages_lues_par_le_modele_sont_rendues_comme_sources(app: FastAPI) -> None:
    """Les passages qui ne tiennent pas dans le contexte ne sont pas transmis ; les
    rendre comme sources ferait croire la réponse adossée à des pages non lues."""
    generations: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1] * 1024]})
        generations.append(json.loads(request.read()))
        return httpx.Response(200, json={"response": REPONSE_GENEREE})

    ollama = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ollama-simule"
    )
    pages = [hit(f"{DOCUMENT_ID}:{n}", page=n, content="x" * 2000) for n in (1, 2, 3)]
    reglages = Settings(ollama_generation_num_ctx=2048, ollama_generation_num_predict=256)
    app.dependency_overrides[get_es] = lambda: FakeEs(hits=pages)
    app.dependency_overrides[get_ollama] = lambda: ollama
    app.dependency_overrides[get_settings] = lambda: reglages
    client = TestClient(app, raise_server_exceptions=False)

    corps = client.post("/search/answer", json={"query": "Combien ?", "top_k": 3}).json()

    assert generations[0]["options"]["num_ctx"] == 2048
    assert generations[0]["options"]["num_predict"] == 256
    assert [source["page_number"] for source in corps["sources"]] == [1]


# --- Pannes : jamais confondues avec une absence de résultat -----------------


def test_ollama_en_panne_donne_503_et_non_une_liste_vide(app: FastAPI) -> None:
    ollama, _ = ollama_simule(embeddings=None)
    app.dependency_overrides[get_es] = lambda: FakeEs(hits=[hit("x:1")])
    app.dependency_overrides[get_ollama] = lambda: ollama
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/search", json={"query": "encre"})

    assert response.status_code == 503
    assert "Ollama" in response.json()["detail"]


def test_un_index_absent_est_dit_et_oriente_vers_reindex(app: FastAPI) -> None:
    """Index absent n'est pas « aucun résultat » : c'est une stack incomplète."""
    ollama, _ = ollama_simule(embeddings=[[0.1] * 1024])
    app.dependency_overrides[get_es] = lambda: FakeEs(absent=True)
    app.dependency_overrides[get_ollama] = lambda: ollama
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/search", json={"query": "encre"})

    assert response.status_code == 503
    assert "reindex" in response.json()["detail"]


def test_une_generation_en_panne_donne_503(app: FastAPI) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1] * 1024]})
        return httpx.Response(500, text="model runner has crashed")

    ollama = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ollama-simule"
    )
    app.dependency_overrides[get_es] = lambda: FakeEs(hits=[hit("x:1")])
    app.dependency_overrides[get_ollama] = lambda: ollama
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/search/answer", json={"query": "Combien ?"})

    assert response.status_code == 503
    assert json.loads(response.content)["detail"]
