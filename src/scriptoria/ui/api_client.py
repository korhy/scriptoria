"""Client HTTP de l'UI.

L'UI ne parle qu'à l'API, jamais à la base : c'est ce qui la rend remplaçable.
Chaque appel rend le code HTTP et le corps, sans masquer les erreurs — une panne
réseau rend le code 0 et sa cause, jamais une liste vide.
"""

import os

import httpx

API_BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")
TIMEOUT_SECONDS = 30.0
# Plafond des listes de documents : l'API en rend 50 par défaut, trop peu pour
# retrouver un document par la liste déroulante.
LISTE_MAX = 500
# Téléverser 200 pages de 25 Mo ne tient pas en 30 s.
IMPORT_TIMEOUT_SECONDS = 600.0
# La génération coûte ~12 s échange de modèle compris, bien plus à froid.
ANSWER_TIMEOUT_SECONDS = 300.0

Reponse = tuple[int, object]


def _corps(response: httpx.Response) -> object:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return {"detail": response.text}


def _appel(methode: str, path: str, timeout: float = TIMEOUT_SECONDS, **kwargs: object) -> Reponse:
    try:
        response = httpx.request(methode, f"{API_BASE_URL}{path}", timeout=timeout, **kwargs)
    except httpx.HTTPError as exc:
        return 0, {"detail": f"API injoignable sur {API_BASE_URL} : {exc}"}
    return response.status_code, _corps(response)


def get(path: str, **params: object) -> Reponse:
    return _appel("GET", path, params=params)


def post(path: str, payload: dict | None = None, timeout: float = TIMEOUT_SECONDS) -> Reponse:
    return _appel("POST", path, timeout=timeout, json=payload)


def delete(path: str) -> Reponse:
    return _appel("DELETE", path)


def importer(fichiers: list[tuple[str, bytes, str]]) -> Reponse:
    """Importe un document : `(nom, octets, type MIME)` par page, **dans l'ordre**."""
    envoi = [("files", fichier) for fichier in fichiers]
    return _appel("POST", "/documents", timeout=IMPORT_TIMEOUT_SECONDS, files=envoi)


def get_image(path: str, **params: object) -> bytes | None:
    """Récupère une image côté serveur.

    Le navigateur du relecteur ne sait pas résoudre `http://api:8000` : c'est
    l'UI qui va chercher les octets et les transmet à Streamlit.
    """
    try:
        response = httpx.get(f"{API_BASE_URL}{path}", params=params, timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return None
    return response.content if response.status_code == 200 else None


def detail(corps: object) -> str:
    """Le message d'erreur de l'API, tel quel : il dit presque toujours quoi faire."""
    if isinstance(corps, dict) and "detail" in corps:
        return str(corps["detail"])
    return str(corps)
