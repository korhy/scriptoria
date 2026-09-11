"""UI de validation image ↔ texte.

À ce stade, l'UI se connecte à l'API et affiche l'état réel de la stack ainsi que
les documents présents. La vue de validation côte à côte est esquissée mais reste
inerte : les routes `/pages/*` renvoient encore 501. Afficher un faux éditeur
donnerait l'illusion d'un pipeline qui fonctionne.

Ne partage aucun code avec l'API : remplaçable par un front React sans toucher
au reste de la stack.
"""

import os

import httpx
import streamlit as st

API_BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")

st.set_page_config(page_title="Scriptoria", page_icon="📄", layout="wide")


@st.cache_data(ttl=5)
def fetch(path: str) -> tuple[int, object]:
    """Appelle l'API. Retourne le code HTTP et le corps, sans masquer les erreurs."""
    try:
        response = httpx.get(f"{API_BASE_URL}{path}", timeout=10.0)
        return response.status_code, response.json()
    except httpx.HTTPError as exc:
        return 0, {"error": str(exc)}


st.title("📄 Scriptoria")
st.caption("Numérisation et RAG documentaire — 100 % local")

# --- État de la stack -------------------------------------------------------
status_code, health = fetch("/health")

if status_code == 0:
    st.error(f"API injoignable sur {API_BASE_URL} — {health.get('error')}")
elif isinstance(health, dict):
    columns = st.columns(3)
    dependencies = [("Postgres", "db"), ("Elasticsearch", "elasticsearch"), ("Ollama", "ollama")]
    for column, (label, key) in zip(columns, dependencies, strict=True):
        column.metric(label, "OK" if health.get(key) else "KO")
    if status_code != 200:
        st.warning("Stack dégradée : une dépendance au moins ne répond pas.")

st.divider()

# --- Documents --------------------------------------------------------------
st.subheader("Documents")
status_code, documents = fetch("/documents")

if status_code == 200 and isinstance(documents, list):
    if documents:
        st.dataframe(documents, use_container_width=True)
    else:
        st.info(
            "Aucun document. L'import n'est pas encore implémenté (`POST /documents` renvoie 501)."
        )
else:
    st.error(f"Lecture des documents impossible (HTTP {status_code}).")

st.divider()

# --- Validation (à venir) ---------------------------------------------------
st.subheader("Validation image ↔ texte")
st.info(
    "Vue de validation non implémentée. Elle affichera l'image de page à gauche, "
    "le Markdown éditable à droite, et la confiance par bloc en surbrillance. "
    "Elle nécessite les routes `/pages/{id}` et `/pages/{id}/image`, aujourd'hui en 501."
)
