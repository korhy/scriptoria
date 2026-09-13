"""UI de Scriptoria : documents, validation, recherche.

Ne partage aucun code avec l'API : remplaçable par un front React sans toucher
au reste de la stack. Les données transitent par HTTP, jamais par la base.

Tout texte venu d'un document ou d'un modèle est une **donnée** : échappé avant
un rendu HTML (Validation), affiché en texte brut ailleurs (Recherche).
"""

import api_client
import streamlit as st

st.set_page_config(page_title="Scriptoria", page_icon="📄", layout="wide")


def render_health() -> None:
    status_code, health = api_client.get("/health")

    if status_code == 0 or not isinstance(health, dict):
        st.error(f"API injoignable sur {api_client.API_BASE_URL}")
        st.stop()

    colonnes = st.columns(3)
    dependances = [("Postgres", "db"), ("Elasticsearch", "elasticsearch"), ("Ollama", "ollama")]
    for colonne, (label, cle) in zip(colonnes, dependances, strict=True):
        colonne.metric(label, "OK" if health.get(cle) else "KO")
    if status_code != 200:
        st.warning("Stack dégradée : une dépendance au moins ne répond pas.")


navigation = st.navigation(
    [
        st.Page(
            "page_documents.py", title="Documents", icon="📥", url_path="documents", default=True
        ),
        st.Page("page_validation.py", title="Validation", icon="📝", url_path="validation"),
        st.Page("page_recherche.py", title="Recherche", icon="🔎", url_path="recherche"),
    ]
)

st.title("📄 Scriptoria")
st.caption("Numérisation et RAG documentaire — 100 % local")
render_health()
st.divider()

navigation.run()
