"""Recherche : une question, puis les passages ou une réponse rédigée.

**Passages et réponse s'affichent en texte brut, jamais en Markdown.** Un passage
vient d'une page scannée ; une réponse le recopie. Rendu en Markdown, un
`![](http://…)` présent sur la page ferait charger une URL externe par le
navigateur — la stack est 100 % locale, l'écran ne doit pas l'être moins.

Chaque source mène à sa page dans la vue Validation : une réponse qu'on ne peut
pas vérifier ne vaut rien sur un fonds dont l'OCR peut se tromper.
"""

import api_client
import streamlit as st
from presentation import libelle_source

MODE_REPONSE = "Réponse rédigée"
MODE_PASSAGES = "Passages seuls"


def ouvrir(hit: dict) -> None:
    st.session_state["cible_validation"] = {
        "document_id": hit["document_id"],
        "page_number": hit["page_number"],
    }
    st.switch_page("page_validation.py")


def render_passages(hits: list[dict], documents: dict[str, dict]) -> None:
    if not hits:
        st.info("Aucun passage ne correspond à la question.")
        return
    # Numérotés dans l'ordre reçu : c'est l'ordre des renvois [1], [2]… de la réponse.
    for position, hit in enumerate(hits, start=1):
        with st.container(border=True):
            titre, bouton = st.columns([4, 1])
            titre.text(f"[{position}] {libelle_source(hit, documents)} · score {hit['score']:.4f}")
            if bouton.button("Ouvrir la page", key=f"ouvrir-{position}", width="stretch"):
                ouvrir(hit)
            st.code(hit["content"], language=None, wrap_lines=True)


def rechercher(question: str, top_k: int, mode: str) -> None:
    payload = {"query": question, "top_k": top_k}
    if mode == MODE_REPONSE:
        with st.spinner("Rédaction… le modèle de génération se charge (~15 s)."):
            code, corps = api_client.post(
                "/search/answer", payload, timeout=api_client.ANSWER_TIMEOUT_SECONDS
            )
    else:
        code, corps = api_client.post("/search", payload)

    if code != 200 or not isinstance(corps, dict):
        # 503 : Ollama éteint ou index absent. Le message dit quoi lancer.
        st.session_state.pop("resultat_recherche", None)
        st.error(f"Recherche impossible (HTTP {code}) : {api_client.detail(corps)}")
        return
    # Conservé en session : cliquer « Ouvrir la page » relance le script.
    st.session_state["resultat_recherche"] = {"mode": mode, "question": question, **corps}


with st.form("recherche"):
    question = st.text_input("Question", placeholder="Quel est le prix unitaire des cartouches ?")
    choix_mode, choix_nombre = st.columns([3, 1])
    mode = choix_mode.radio("Résultat", [MODE_REPONSE, MODE_PASSAGES], horizontal=True)
    top_k = choix_nombre.number_input("Passages", min_value=1, max_value=20, value=5)
    lancee = st.form_submit_button("Rechercher", type="primary")

if lancee:
    if question.strip():
        rechercher(question.strip(), int(top_k), mode)
    else:
        st.warning("Saisir une question.")

resultat = st.session_state.get("resultat_recherche")
if resultat:
    code, liste = api_client.get("/documents", limit=api_client.LISTE_MAX)
    documents = {d["id"]: d for d in liste} if code == 200 and isinstance(liste, list) else {}

    st.caption(f"Question : {resultat['question']}")
    if resultat["mode"] == MODE_REPONSE:
        st.subheader("Réponse")
        st.text(resultat["answer"])
        st.subheader("Sources")
        render_passages(resultat["sources"], documents)
    else:
        st.subheader("Passages")
        render_passages(resultat["hits"], documents)
