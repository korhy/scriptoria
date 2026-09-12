"""UI de validation image ↔ texte.

Vue côte à côte : l'image de la page à gauche, le Markdown éditable à droite, les
fragments de faible confiance mis en avant. Valider ou corriger appelle
`POST /pages/{id}/corrections`, qui crée une révision `n+1` — l'UI n'écrase
jamais rien, elle ne peut que faire ajouter.

Ne partage aucun code avec l'API : remplaçable par un front React sans toucher
au reste de la stack. Les données transitent par HTTP, jamais par la base.

**Le Markdown affiché vient d'un LLM lisant un document inconnu : c'est une
donnée.** Il est échappé avant tout rendu HTML, faute de quoi une page scannée
portant du balisage ferait exécuter n'importe quoi dans le navigateur du relecteur.
"""

import html
import os

import httpx
import streamlit as st

API_BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")
TIMEOUT_SECONDS = 30.0

# Aligné sur CONFIDENCE_SECOND_PASS_THRESHOLD : en dessous, la page mérite l'œil.
LOW_CONFIDENCE = 0.5

STATUS_LABELS = {
    "new": "importé",
    "preprocessing": "prétraitement…",
    "preprocessed": "prétraité",
    "transcribing": "OCR en cours…",
    "awaiting_validation": "à valider",
    "validated": "validé",
    "indexed": "indexé",
    "failed": "en échec",
}

st.set_page_config(page_title="Scriptoria", page_icon="📄", layout="wide")


def api_get(path: str, **params: str) -> tuple[int, object]:
    """Appelle l'API. Retourne le code HTTP et le corps, sans masquer les erreurs."""
    try:
        response = httpx.get(f"{API_BASE_URL}{path}", params=params, timeout=TIMEOUT_SECONDS)
        return response.status_code, response.json()
    except httpx.HTTPError as exc:
        return 0, {"error": str(exc)}


def api_get_image(path: str, **params: str) -> bytes | None:
    """Récupère une image côté serveur.

    Le navigateur du relecteur ne sait pas résoudre `http://api:8000` : c'est
    l'UI qui va chercher les octets et les transmet à Streamlit.
    """
    try:
        response = httpx.get(f"{API_BASE_URL}{path}", params=params, timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return None
    return response.content if response.status_code == 200 else None


def api_post(path: str, payload: dict | None = None) -> tuple[int, object]:
    try:
        response = httpx.post(f"{API_BASE_URL}{path}", json=payload, timeout=TIMEOUT_SECONDS)
        return response.status_code, response.json()
    except httpx.HTTPError as exc:
        return 0, {"error": str(exc)}


def libelle_document(document: dict) -> str:
    statut = STATUS_LABELS.get(document["status"], document["status"])
    return f"{document['source_filename']} — {document['page_count']} p. — {statut}"


def render_health() -> None:
    status_code, health = api_get("/health")

    if status_code == 0 or not isinstance(health, dict):
        st.error(f"API injoignable sur {API_BASE_URL}")
        st.stop()

    colonnes = st.columns(3)
    dependances = [("Postgres", "db"), ("Elasticsearch", "elasticsearch"), ("Ollama", "ollama")]
    for colonne, (label, cle) in zip(colonnes, dependances, strict=True):
        colonne.metric(label, "OK" if health.get(cle) else "KO")
    if status_code != 200:
        st.warning("Stack dégradée : une dépendance au moins ne répond pas.")


def render_fragments_douteux(transcription: dict, texte: str) -> None:
    """Met en avant les fragments que les contrôles de confiance ont signalés."""
    blocs = [
        bloc
        for bloc in transcription.get("confidence_blocks", [])
        if bloc["score"] < LOW_CONFIDENCE
    ]
    if not blocs:
        st.success(
            "Aucun signal d'alerte. Ce qui ne prouve pas l'exactitude : à relire quand même."
        )
        return

    st.warning(f"{len(blocs)} fragment(s) à vérifier en priorité")
    for bloc in sorted(blocs, key=lambda bloc: bloc["score"]):
        extrait = texte[bloc["start_offset"] : bloc["end_offset"]]
        # Échappé : ce texte vient du modèle, pas de nous.
        st.markdown(
            f"- `{bloc['method']}` · score **{bloc['score']:.2f}** · "
            f"<mark>{html.escape(extrait)}</mark>",
            unsafe_allow_html=True,
        )


def render_historique(transcriptions: list[dict]) -> None:
    """Toutes les révisions, de la plus ancienne à la plus récente.

    L'historique est l'objet même du modèle de données : le montrer rappelle
    qu'aucune correction n'a effacé ce qui la précédait.
    """
    lignes = [
        {
            "révision": transcription["revision"],
            "origine": transcription["origin"],
            "modèle": transcription["model_name"] or "—",
            "validée": "oui" if transcription["is_validated"] else "non",
            "créée le": transcription["created_at"][:19].replace("T", " "),
        }
        for transcription in transcriptions
    ]
    st.dataframe(lignes, use_container_width=True, hide_index=True)


def render_validation(page: dict) -> None:
    status_code, detail = api_get(f"/pages/{page['id']}")
    if status_code != 200 or not isinstance(detail, dict):
        st.error(f"Lecture de la page impossible (HTTP {status_code}).")
        return

    transcriptions = detail["transcriptions"]
    if not transcriptions:
        st.info(
            "Aucune transcription pour cette page. Lancer l'OCR : "
            "`POST /documents/{id}/transcribe`."
        )
        return

    derniere = transcriptions[-1]
    score = detail.get("confidence_score")

    image, texte = st.columns([1, 1], gap="large")

    with image:
        variante = st.radio(
            "Image", ["preprocessed", "raw"], horizontal=True, label_visibility="collapsed"
        )
        octets = api_get_image(
            f"/documents/{detail['document_id']}/pages/{detail['page_number']}/image",
            variant=variante,
        )
        if octets is None:
            st.error("Image indisponible.")
        else:
            st.image(octets, use_container_width=True)

    with texte:
        entete = st.columns([1, 2])
        entete[0].metric("Confiance", "—" if score is None else f"{score:.2f}")
        entete[1].caption(
            f"Révision {derniere['revision']} · origine {derniere['origin']}"
            f"{' · validée' if derniere['is_validated'] else ''}"
        )

        render_fragments_douteux(derniere, derniere["content_markdown"])

        corrige = st.text_area(
            "Markdown de la page",
            value=derniere["content_markdown"],
            height=360,
            key=f"markdown-{page['id']}-{derniere['revision']}",
        )

        inchange = corrige.strip() == derniere["content_markdown"].strip()
        boutons = st.columns(2)
        valider = boutons[0].button(
            "✓ Valider tel quel", type="primary", disabled=not inchange, use_container_width=True
        )
        corriger = boutons[1].button(
            "✎ Enregistrer la correction", disabled=inchange, use_container_width=True
        )

        if valider or corriger:
            code, reponse = api_post(
                f"/pages/{page['id']}/corrections",
                {"content_markdown": corrige, "validate_now": True},
            )
            if code != 201:
                st.error(f"Enregistrement refusé (HTTP {code}) : {reponse}")
            else:
                revision = reponse["revision"] if isinstance(reponse, dict) else "?"
                st.success(f"Révision {revision} créée — la précédente est conservée.")
                st.rerun()

        st.divider()
        st.caption("Historique des révisions")
        render_historique(transcriptions)


st.title("📄 Scriptoria")
st.caption("Numérisation et RAG documentaire — 100 % local")

render_health()
st.divider()

status_code, documents = api_get("/documents")
if status_code != 200 or not isinstance(documents, list):
    st.error(f"Lecture des documents impossible (HTTP {status_code}).")
    st.stop()

if not documents:
    st.info("Aucun document. Importer des pages : `POST /documents` (une image par page).")
    st.stop()

# Les listes déroulantes portent sur des **identifiants**, jamais sur les
# dictionnaires reçus de l'API. Streamlit restaure une sélection par égalité de
# valeur ; or ces dictionnaires sont reconstruits à chaque rerun et changent dès
# qu'un champ bouge — `updated_at` change précisément au moment d'une validation.
# Un identifiant, lui, reste égal à lui-même. Le risque évité n'est pas cosmétique :
# une sélection mal restaurée ferait valider une autre page que celle affichée.
documents_par_id = {document["id"]: document for document in documents}
document_id = st.selectbox(
    "Document",
    list(documents_par_id),
    format_func=lambda identifiant: libelle_document(documents_par_id[identifiant]),
    key="document_id",
)

status_code, pages = api_get(f"/documents/{document_id}/pages")
if status_code != 200 or not isinstance(pages, list) or not pages:
    st.warning("Ce document n'a aucune page lisible.")
    st.stop()

pages_par_id = {page["id"]: page for page in pages}
page_id = st.selectbox(
    "Page",
    list(pages_par_id),
    format_func=lambda identifiant: f"page {pages_par_id[identifiant]['page_number']}",
    key=f"page_id-{document_id}",
)
page = pages_par_id[page_id]

st.divider()
render_validation(page)
