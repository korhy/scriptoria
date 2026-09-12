"""Retranscription d'une page par le modèle vision.

Aucun test ici ne sort de la machine : le transport httpx est simulé, si bien
que la forme exacte de la requête envoyée à Ollama est vérifiable sans Ollama.
Le chemin réel — modèle chargé, vraie page — relève d'un test marqué `ollama`.
"""

import base64
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from scriptoria.services.ocr import DEFAULT_PROMPT, OcrError, transcribe_page

CONTENU_IMAGE = b"\x89PNG\r\n\x1a\ncontenu-binaire-quelconque"


@pytest.fixture
def image(tmp_path: Path) -> Path:
    chemin = tmp_path / "0001.png"
    chemin.write_bytes(CONTENU_IMAGE)
    return chemin


def client_simule(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    """Client dont les requêtes n'atteignent jamais le réseau."""
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ollama-simule"
    )


def capture(markdown: str = "texte") -> tuple[Callable, list[dict]]:
    """Handler simulé et liste des corps de requête qu'il a reçus."""
    corps: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        corps.append(json.loads(request.read()))
        return httpx.Response(200, json={"response": markdown, "done": True})

    return handler, corps


# --- Forme de la requête ----------------------------------------------------


async def test_l_image_part_en_base64_vers_le_modele_demande(image: Path) -> None:
    """Ollama attend les images encodées dans le corps JSON, pas en multipart."""
    handler, corps_envoyes = capture()

    async with client_simule(handler) as client:
        await transcribe_page(client, image, model="qwen2.5vl:7b")

    corps = corps_envoyes[0]
    assert corps["model"] == "qwen2.5vl:7b"
    assert base64.b64decode(corps["images"][0]) == CONTENU_IMAGE


async def test_l_appel_vise_l_endpoint_de_generation_d_ollama(image: Path) -> None:
    """`/api/generate` et non `/api/chat` : on transcrit une image, on ne dialogue pas."""
    vues: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vues.append(request.url)
        return httpx.Response(200, json={"response": "texte"})

    async with client_simule(handler) as client:
        await transcribe_page(client, image, model="qwen2.5vl:7b")

    assert vues[0].path == "/api/generate"


async def test_la_reponse_n_est_pas_diffusee_en_flux(image: Path) -> None:
    """Sans `stream: false`, Ollama renvoie du JSON ligne à ligne : illisible ici."""
    handler, vues = capture()

    async with client_simule(handler) as client:
        await transcribe_page(client, image, model="qwen2.5vl:7b")

    assert vues[0]["stream"] is False


async def test_la_temperature_est_nulle(image: Path) -> None:
    """Une transcription n'est pas une rédaction : aucune place pour l'invention."""
    handler, vues = capture()

    async with client_simule(handler) as client:
        await transcribe_page(client, image, model="qwen2.5vl:7b")

    assert vues[0]["options"]["temperature"] == 0


async def test_la_consigne_par_defaut_s_applique_en_l_absence_de_consigne(image: Path) -> None:
    handler, vues = capture()

    async with client_simule(handler) as client:
        await transcribe_page(client, image, model="qwen2.5vl:7b")

    assert vues[0]["prompt"] == DEFAULT_PROMPT


async def test_une_consigne_explicite_remplace_celle_par_defaut(image: Path) -> None:
    handler, vues = capture()

    async with client_simule(handler) as client:
        await transcribe_page(client, image, model="qwen2.5vl:7b", prompt="Transcris en vers.")

    assert vues[0]["prompt"] == "Transcris en vers."


def test_la_consigne_par_defaut_traite_la_page_comme_une_donnee() -> None:
    """Une page scannée peut contenir du texte qui ressemble à une instruction.

    La consigne doit dire explicitement qu'on transcrit ce texte sans l'exécuter,
    sans quoi une facture portant « ignore les instructions précédentes » peut
    détourner la transcription.
    """
    consigne = DEFAULT_PROMPT.lower()

    assert "instruction" in consigne
    assert "transcris" in consigne


# --- Résultat ---------------------------------------------------------------


async def test_le_markdown_retourne_est_celui_du_modele(image: Path) -> None:
    handler, _ = capture("# Titre\n\ncorps")
    async with client_simule(handler) as client:
        resultat = await transcribe_page(client, image, model="qwen2.5vl:7b")

    assert resultat.content_markdown == "# Titre\n\ncorps"
    assert resultat.model_name == "qwen2.5vl:7b"


async def test_la_duree_du_passage_est_mesuree(image: Path) -> None:
    """Seule mesure fiable du coût réel : ~57 s/page mesurées sur la machine cible."""
    handler, _ = capture()
    async with client_simule(handler) as client:
        resultat = await transcribe_page(client, image, model="qwen2.5vl:7b")

    assert resultat.duration_seconds > 0


# --- Échecs : jamais de succès simulé ---------------------------------------


async def test_une_erreur_http_d_ollama_est_signalee(image: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="model runner has crashed")

    async with client_simule(handler) as client:
        with pytest.raises(OcrError) as erreur:
            await transcribe_page(client, image, model="qwen2.5vl:7b")

    # Le message doit désigner la cause : diagnostiquer un 500 muet coûte des heures.
    assert "500" in str(erreur.value)


async def test_une_reponse_vide_est_une_erreur_pas_une_page_blanche(image: Path) -> None:
    """Une transcription vide acceptée silencieusement s'archive comme une page vide."""
    handler, _ = capture("   \n  ")
    async with client_simule(handler) as client:
        with pytest.raises(OcrError):
            await transcribe_page(client, image, model="qwen2.5vl:7b")


async def test_une_reponse_sans_champ_response_est_une_erreur(image: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "model not found"})

    async with client_simule(handler) as client:
        with pytest.raises(OcrError) as erreur:
            await transcribe_page(client, image, model="absent:7b")

    assert "absent:7b" in str(erreur.value)


async def test_une_image_absente_est_signalee_avant_tout_appel(tmp_path: Path) -> None:
    appels: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        appels.append(request)
        return httpx.Response(200, json={"response": "texte"})

    async with client_simule(handler) as client:
        with pytest.raises(OcrError) as erreur:
            await transcribe_page(client, tmp_path / "absente.png", model="qwen2.5vl:7b")

    assert "absente.png" in str(erreur.value)
    assert appels == [], "aucune requête ne doit partir si l'image n'existe pas"


async def test_ollama_injoignable_est_signale_avec_son_adresse(image: Path) -> None:
    """Ollama tourne sur l'hôte, hors Docker : l'oublier est la panne la plus banale."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with client_simule(handler) as client:
        with pytest.raises(OcrError) as erreur:
            await transcribe_page(client, image, model="qwen2.5vl:7b")

    assert "ollama-simule" in str(erreur.value)


# --- Nettoyage de la sortie du modèle ---------------------------------------


async def test_la_cloture_de_code_enveloppant_toute_la_page_est_retiree(image: Path) -> None:
    """Observé sur qwen2.5vl:7b : il rend la page dans un bloc ```markdown.

    Cette clôture est un artefact de conversation, pas du contenu de la page.
    La laisser l'archiverait comme du texte et la ferait ressortir en recherche.
    """
    handler, _ = capture("```markdown\nFACTURE N° 2019-0447\n\n| Qté | PU |\n```")

    async with client_simule(handler) as client:
        resultat = await transcribe_page(client, image, model="qwen2.5vl:7b")

    assert resultat.content_markdown == "FACTURE N° 2019-0447\n\n| Qté | PU |"


async def test_une_page_contenant_du_code_garde_ses_clotures(image: Path) -> None:
    """Une page qui montre du code en contient légitimement : ne pas y toucher."""
    page_avec_code = "Extrait du listing :\n\n```python\nprint('bonjour')\n```\n\nFin."
    handler, _ = capture(page_avec_code)

    async with client_simule(handler) as client:
        resultat = await transcribe_page(client, image, model="qwen2.5vl:7b")

    assert resultat.content_markdown == page_avec_code


async def test_une_transcription_sans_cloture_est_rendue_telle_quelle(image: Path) -> None:
    handler, _ = capture("# Titre\n\ncorps de page")

    async with client_simule(handler) as client:
        resultat = await transcribe_page(client, image, model="qwen2.5vl:7b")

    assert resultat.content_markdown == "# Titre\n\ncorps de page"
