"""Fixtures des tests d'intégration.

Ces tests parlent à la **vraie stack** (`make up`) : API, Postgres,
Elasticsearch, et Ollama sur l'hôte. Ils sont lents par nature — une page OCRisée
coûte ~35 s sur la machine cible — d'où un document de référence mené une seule
fois jusqu'à l'index et partagé par toute la session.

Ce qu'ils apportent que les tests unitaires ne peuvent pas : les doubles y
remplacent la base, l'index et les modèles. Un champ mal nommé dans une requête
Elasticsearch, une migration oubliée ou un modèle absent ne se voient qu'ici.
"""

import os
import socket
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

API_BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")
ES_URL = os.environ.get("ELASTICSEARCH_URL", "http://elasticsearch:9200")

FIXTURE_PAGE = Path(__file__).parent.parent / "fixtures" / "page-test.png"

# L'OCR d'une page dépasse la minute à froid, la génération une dizaine de
# secondes : les délais d'attente sont larges à dessein.
OCR_TIMEOUT = 600.0
STEP_TIMEOUT = 180.0


@pytest.fixture(scope="session")
def api() -> httpx.Client:
    """Client HTTP vers l'API, avec un délai compatible avec l'OCR."""
    with httpx.Client(base_url=API_BASE_URL, timeout=OCR_TIMEOUT) as client:
        yield client


@pytest.fixture(scope="session")
def es() -> httpx.Client:
    """Client HTTP vers Elasticsearch — interrogé directement, sans passer par l'API.

    C'est délibéré : vérifier l'index par l'API reviendrait à se fier au code
    qu'on cherche justement à éprouver.
    """
    with httpx.Client(base_url=ES_URL, timeout=60.0) as client:
        yield client


def attendre_statut(api: httpx.Client, document_id: str, attendu: str, timeout: float) -> dict:
    """Scrute un document jusqu'à l'état voulu. Le worker travaille en arrière-plan."""
    echeance = time.monotonic() + timeout
    dernier: dict = {}
    while time.monotonic() < echeance:
        dernier = api.get(f"/documents/{document_id}").json()
        if dernier["status"] == attendu:
            return dernier
        if dernier["status"] == "failed":
            pytest.fail(f"le document est passé en échec: {dernier}")
        time.sleep(2.0)
    pytest.fail(f"statut '{attendu}' jamais atteint en {timeout}s, dernier: {dernier}")
    raise AssertionError  # inatteignable, pour le typage


@pytest.fixture(scope="session")
def attendre(api: httpx.Client):
    """`attendre_statut` déjà relié au client : évite de le passer partout."""

    def _attendre(document_id: str, statut: str, timeout: float = STEP_TIMEOUT) -> dict:
        return attendre_statut(api, document_id, statut, timeout)

    return _attendre


def importer_page(api: httpx.Client, nom: str = "page.png") -> dict:
    """Importe la page de référence et rend le document créé."""
    fichiers = [("files", (nom, FIXTURE_PAGE.read_bytes(), "image/png"))]
    response = api.post("/documents", files=fichiers)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(scope="session")
def importer(api: httpx.Client):
    """`importer_page` déjà relié au client — évite un import entre modules de test."""

    def _importer(nom: str = "page.png") -> dict:
        return importer_page(api, nom)

    return _importer


def premiere_page(api: httpx.Client, document_id: str) -> dict:
    """Première page d'un document, avec un échec lisible si la lecture rate.

    L'API renvoie un objet `{"detail": ...}` en cas d'erreur : indexer
    aveuglément le premier élément produirait un `KeyError: 0` qui ne dit rien
    de la cause.
    """
    response = api.get(f"/documents/{document_id}/pages")
    assert response.status_code == 200, f"HTTP {response.status_code} : {response.text}"

    pages = response.json()
    assert isinstance(pages, list), f"réponse inattendue : {pages}"
    assert pages, f"aucune page pour le document {document_id}"
    return pages[0]


def _ollama_disponible(api: httpx.Client) -> bool:
    try:
        return bool(api.get("/health").json().get("ollama"))
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="session")
def stack_prete(api: httpx.Client) -> None:
    """Saute toute la série si la stack n'est pas démarrée."""
    try:
        api.get("/health/live")
    except httpx.HTTPError as exc:
        pytest.skip(f"API injoignable sur {API_BASE_URL} — `make up` ? ({exc})")


@pytest.fixture(scope="session")
def ollama_pret(api: httpx.Client, stack_prete: None) -> None:
    if not _ollama_disponible(api):
        pytest.skip("Ollama injoignable — `ollama serve` sur l'hôte, puis `make models`")


@pytest.fixture(scope="session")
def document_indexe(api: httpx.Client, ollama_pret: None, attendre) -> dict[str, Any]:
    """Un document réel mené de l'import à l'index : OCR, validation, indexation.

    Une seule fois pour toute la session : chaque passage OCR coûte ~35 s, et
    toutes les assertions en aval portent sur le même document.

    La validation reprend **le texte de l'OCR tel quel** — c'est le geste d'un
    relecteur qui approuve sans corriger, et il crée quand même une révision.
    """
    document = importer_page(api, nom="reference.png")
    attendre(document["id"], "preprocessed")

    assert api.post(f"/documents/{document['id']}/transcribe").status_code == 202
    transcrit = attendre(document["id"], "awaiting_validation", timeout=OCR_TIMEOUT)
    # La progression doit être complète au moment où l'OCR rend la main : un
    # compteur en retard ferait croire à un lot interrompu.
    assert transcrit["pages_transcribed"] == transcrit["page_count"], transcrit

    page = premiere_page(api, document["id"])
    detail = api.get(f"/pages/{page['id']}").json()
    texte_ocr = detail["transcriptions"][-1]["content_markdown"]

    validation = api.post(
        f"/pages/{page['id']}/corrections",
        json={"content_markdown": texte_ocr, "validate_now": True},
    )
    assert validation.status_code == 201, validation.text
    attendre(document["id"], "indexed")

    return {"document_id": document["id"], "page_id": page["id"], "texte_ocr": texte_ocr}


def attendre_fragment(
    es: httpx.Client, document_id: str, contient: str, timeout: float = STEP_TIMEOUT
) -> dict:
    """Scrute l'index jusqu'à ce que le fragment d'une page porte ce texte.

    Attendre le statut `indexed` ne suffit pas quand le document l'était déjà :
    on ne distingue pas « réindexé » de « toujours indexé ». La seule propriété
    observable est le contenu du fragment lui-même.
    """
    echeance = time.monotonic() + timeout
    dernier: dict = {}
    while time.monotonic() < echeance:
        reponse = es.post(
            "/scriptoria-chunks/_search",
            json={"query": {"term": {"document_id": document_id}}},
        ).json()
        hits = reponse.get("hits", {}).get("hits", [])
        if len(hits) == 1 and contient in hits[0]["_source"]["content"]:
            return hits[0]
        dernier = reponse
        time.sleep(2.0)
    pytest.fail(f"fragment portant '{contient}' jamais indexé en {timeout}s : {dernier}")
    raise AssertionError  # inatteignable, pour le typage


@pytest.fixture(scope="session")
def attendre_indexation(es: httpx.Client):
    """`attendre_fragment` déjà relié au client Elasticsearch."""

    def _attendre(document_id: str, contient: str, timeout: float = STEP_TIMEOUT) -> dict:
        return attendre_fragment(es, document_id, contient, timeout)

    return _attendre


@pytest.fixture(scope="session")
def creer_document_indexe(api: httpx.Client, importer, attendre):
    """Fabrique un document indexé dont le **texte est saisi**, sans passer par l'OCR.

    Deux raisons : le contenu est précisément ce que le test veut maîtriser, et
    cela épargne les ~35 s d'un passage vision. Les tests qui *modifient* un
    document passent par ici plutôt que de muter le document de référence, sans
    quoi leur ordre d'exécution déciderait du résultat.
    """

    def _creer(texte: str, nom: str = "saisie.png") -> dict[str, Any]:
        document = importer(nom)
        page = premiere_page(api, document["id"])
        correction = api.post(
            f"/pages/{page['id']}/corrections",
            json={"content_markdown": texte, "validate_now": True},
        )
        assert correction.status_code == 201, correction.text
        indexe = attendre(document["id"], "indexed")
        # Texte saisi à la main : aucune révision OCR, donc aucune page transcrite.
        # C'est ce qui prouve que le comptage filtre sur l'origine.
        assert indexe["pages_transcribed"] == 0, indexe
        return {"document_id": document["id"], "page_id": page["id"]}

    return _creer


@pytest.fixture(scope="session")
def base_accessible() -> None:
    """Saute les tests qui touchent Postgres directement s'il n'est pas joignable.

    `reindex_all` ouvre ses propres connexions depuis le processus de test : il
    doit tourner là où les noms d'hôtes Docker se résolvent, donc dans le
    conteneur `api` (`make test`), pas depuis l'hôte.
    """
    from scriptoria.config import get_settings

    hote = get_settings().database_url.split("@")[-1].split(":")[0]
    try:
        socket.getaddrinfo(hote, None)
    except OSError:
        pytest.skip(f"'{hote}' ne se résout pas ici — lancer depuis le conteneur api")
