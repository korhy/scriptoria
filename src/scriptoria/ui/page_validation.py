"""Validation image ↔ texte.

Vue côte à côte : l'image de la page à gauche, le Markdown éditable à droite, les
fragments de faible confiance mis en avant. Valider ou corriger appelle
`POST /pages/{id}/corrections`, qui crée une révision `n+1` — l'UI n'écrase
jamais rien, elle ne peut que faire ajouter.

**Le Markdown affiché vient d'un LLM lisant un document inconnu : c'est une
donnée.** Il est échappé avant tout rendu HTML, faute de quoi une page scannée
portant du balisage ferait exécuter n'importe quoi dans le navigateur du relecteur.
"""

import html

import api_client
import streamlit as st
from presentation import cle_widget, index_selection, libelle_document

# Aligné sur CONFIDENCE_SECOND_PASS_THRESHOLD : en dessous, la page mérite l'œil.
LOW_CONFIDENCE = 0.5


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
    st.dataframe(lignes, width="stretch", hide_index=True)


def render_validation(page: dict) -> None:
    status_code, detail = api_client.get(f"/pages/{page['id']}")
    if status_code != 200 or not isinstance(detail, dict):
        st.error(f"Lecture de la page impossible (HTTP {status_code}).")
        return

    transcriptions = detail["transcriptions"]
    if not transcriptions:
        st.info("Aucune transcription pour cette page : lancer l'OCR depuis la page Documents.")
        return

    derniere = transcriptions[-1]
    score = detail.get("confidence_score")

    image, texte = st.columns([1, 1], gap="large")

    with image:
        variante = st.radio(
            "Image", ["preprocessed", "raw"], horizontal=True, label_visibility="collapsed"
        )
        octets = api_client.get_image(
            f"/documents/{detail['document_id']}/pages/{detail['page_number']}/image",
            variant=variante,
        )
        if octets is None:
            st.error("Image indisponible.")
        else:
            st.image(octets, width="stretch")

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
            "✓ Valider tel quel", type="primary", disabled=not inchange, width="stretch"
        )
        corriger = boutons[1].button(
            "✎ Enregistrer la correction", disabled=inchange, width="stretch"
        )

        if valider or corriger:
            code, reponse = api_client.post(
                f"/pages/{page['id']}/corrections",
                {"content_markdown": corrige, "validate_now": True},
            )
            if code != 201:
                st.error(f"Enregistrement refusé (HTTP {code}) : {api_client.detail(reponse)}")
            else:
                revision = reponse["revision"] if isinstance(reponse, dict) else "?"
                st.success(f"Révision {revision} créée — la précédente est conservée.")
                st.rerun()

        st.divider()
        st.caption("Historique des révisions")
        render_historique(transcriptions)


# Une autre vue (Documents, Recherche) peut demander l'ouverture d'une page précise.
cible = st.session_state.pop("cible_validation", None)
if cible:
    st.session_state["validation_document"] = cible["document_id"]
    # Un tour neuf force des widgets neufs : la page demandée l'emporte sur la
    # sélection que la vue gardait.
    st.session_state["validation_tour"] = st.session_state.get("validation_tour", 0) + 1
tour = st.session_state.get("validation_tour", 0)

status_code, documents = api_client.get("/documents", limit=api_client.LISTE_MAX)
if status_code != 200 or not isinstance(documents, list):
    st.error(f"Lecture des documents impossible (HTTP {status_code}).")
    st.stop()

if not documents:
    st.info("Aucun document. En importer depuis la page Documents.")
    st.stop()

# Les listes déroulantes portent sur des **identifiants**, jamais sur les
# dictionnaires reçus de l'API. Streamlit restaure une sélection par égalité de
# valeur ; or ces dictionnaires sont reconstruits à chaque rerun et changent dès
# qu'un champ bouge — `updated_at` change précisément au moment d'une validation.
# Un identifiant, lui, reste égal à lui-même. Le risque évité n'est pas cosmétique :
# une sélection mal restaurée ferait valider une autre page que celle affichée.
# Pour la même raison, la clé des widgets suit la liste (`cle_widget`) : sous une
# clé inchangée, le libellé affiché peut survivre à l'élément qu'il désignait.
documents_par_id = {document["id"]: document for document in documents}
identifiants = list(documents_par_id)
voulu = st.session_state.get("validation_document")
if cible and voulu not in documents_par_id:
    st.warning("Le document demandé n'est plus disponible.")

document_id = st.selectbox(
    "Document",
    identifiants,
    index=index_selection(identifiants, voulu),
    format_func=lambda identifiant: libelle_document(documents_par_id[identifiant]),
    key=cle_widget(f"validation_document-{tour}", identifiants),
)
st.session_state["validation_document"] = document_id

status_code, pages = api_client.get(f"/documents/{document_id}/pages")
if status_code != 200 or not isinstance(pages, list) or not pages:
    st.warning("Ce document n'a aucune page lisible.")
    st.stop()

pages_par_id = {page["id"]: page for page in pages}
page_ids = list(pages_par_id)
memoire_page = f"validation_page-{document_id}"
if cible and cible["document_id"] == document_id:
    st.session_state[memoire_page] = next(
        (page["id"] for page in pages if page["page_number"] == cible["page_number"]), None
    )

page_id = st.selectbox(
    "Page",
    page_ids,
    index=index_selection(page_ids, st.session_state.get(memoire_page)),
    format_func=lambda identifiant: f"page {pages_par_id[identifiant]['page_number']}",
    key=cle_widget(f"validation_page-{tour}-{document_id}", page_ids),
)
st.session_state[memoire_page] = page_id
page = pages_par_id[page_id]

st.divider()
render_validation(page)
