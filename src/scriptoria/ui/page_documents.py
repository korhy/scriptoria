"""Documents : import, suivi de l'OCR, relance et suppression.

Tout passe par l'API. L'UI propose, l'API décide : un refus (409) est affiché
avec son message, qui dit presque toujours quoi faire.
"""

import time

import api_client
import streamlit as st
from presentation import (
    action_ocr,
    cle_naturelle,
    cle_widget,
    en_cours,
    index_selection,
    libelle_document,
    progression,
)

# Aligné sur `ALLOWED_IMAGE_SUFFIXES` côté API, qui reste seule juge.
EXTENSIONS = ["png", "jpg", "jpeg", "tif", "tiff", "webp", "bmp"]
RAFRAICHISSEMENT_SECONDES = 5
# Le worker ne prend pas un job à l'instant : juste après un envoi, le document
# garde son statut et rien n'est « en cours ». Le tableau reste vivant le temps
# que le worker s'en saisisse.
ATTENTE_WORKER_SECONDES = 30
_APRES_OCR = {"awaiting_validation", "validated", "indexed"}


def annoncer(message: str) -> None:
    """Message à afficher après le rerun qui suit une action."""
    st.session_state["annonce_documents"] = message


def afficher_annonce() -> None:
    if message := st.session_state.pop("annonce_documents", None):
        st.success(message)


def lire_documents() -> list[dict] | None:
    code, documents = api_client.get("/documents", limit=api_client.LISTE_MAX)
    if code != 200 or not isinstance(documents, list):
        st.error(f"Lecture des documents impossible (HTTP {code}) : {api_client.detail(documents)}")
        return None
    return documents


def render_import() -> None:
    st.subheader("Importer un document")
    st.caption("Une image par page. Les pages suivent l'ordre naturel des noms de fichiers.")

    # Changer de clé vide le sélecteur une fois l'import accepté.
    tour = st.session_state.setdefault("import_tour", 0)
    fichiers = st.file_uploader(
        "Pages", type=EXTENSIONS, accept_multiple_files=True, key=f"import-{tour}"
    )
    if not fichiers:
        return

    ordonnes = sorted(fichiers, key=lambda fichier: cle_naturelle(fichier.name))
    st.text("Ordre des pages : " + " · ".join(f"{n}. {f.name}" for n, f in enumerate(ordonnes, 1)))

    if not st.button(f"Importer {len(ordonnes)} page(s)", type="primary"):
        return

    with st.spinner("Téléversement…"):
        code, corps = api_client.importer(
            [(f.name, f.getvalue(), f.type or "application/octet-stream") for f in ordonnes]
        )
    if code != 201 or not isinstance(corps, dict):
        st.error(f"Import refusé (HTTP {code}) : {api_client.detail(corps)}")
        return

    st.session_state["import_tour"] = tour + 1
    st.session_state["document_choisi"] = corps["id"]
    annoncer(f"{corps['source_filename']} importé : prétraitement en file.")
    st.rerun()


def _statuts(documents: list[dict]) -> dict[str, str]:
    return {document["id"]: document["status"] for document in documents}


def _tableau() -> list[dict] | None:
    documents = lire_documents()
    if documents is None:
        return None
    lignes = [
        {
            "fichier": document["source_filename"],
            "état": progression(document).libelle,
            "progression": progression(document).fraction,
            "pages": document["page_count"],
            "importé le": document["created_at"][:16].replace("T", " "),
        }
        for document in documents
    ]
    st.dataframe(
        lignes,
        hide_index=True,
        width="stretch",
        column_config={
            "progression": st.column_config.ProgressColumn(
                "progression", min_value=0.0, max_value=1.0, format="percent"
            )
        },
    )
    return documents


@st.fragment(run_every=RAFRAICHISSEMENT_SECONDES)
def _tableau_vivant() -> None:
    """Tableau rafraîchi seul, et page entière relancée dès qu'un statut change.

    Le panneau d'actions est hors du fragment. Sans relance complète, il
    garderait l'état du dernier rendu : un document devenu `preprocessed`
    resterait affiché « importé », avec « Lancer l'OCR » grisé.
    """
    documents = _tableau()
    if documents is not None and _statuts(documents) != st.session_state.get("statuts_rendus"):
        st.rerun(scope="app")


def render_actions(documents: list[dict]) -> None:
    st.subheader("Agir sur un document")
    par_id = {document["id"]: document for document in documents}

    # Sélection par identifiant, jamais par ligne de tableau : l'ordre des lignes
    # change à chaque import, et une ligne mal restaurée ferait supprimer un
    # autre document que celui affiché. Pour la même raison, la clé du widget suit
    # la liste (`cle_widget`) et le choix est gardé à part.
    identifiants = list(par_id)
    document_id = st.selectbox(
        "Document",
        identifiants,
        index=index_selection(identifiants, st.session_state.get("document_choisi")),
        format_func=lambda identifiant: libelle_document(par_id[identifiant]),
        key=cle_widget("document_actions", identifiants),
    )
    st.session_state["document_choisi"] = document_id
    document = par_id[document_id]
    etat = progression(document)
    st.progress(etat.fraction, text=etat.libelle)

    ocr, relire, supprimer = st.columns(3)

    action = action_ocr(document)
    libelle_ocr = "↻ Relancer l'OCR" if action == "relancer" else "▶ Lancer l'OCR"
    if ocr.button(libelle_ocr, disabled=action is None, width="stretch"):
        code, corps = api_client.post(f"/documents/{document_id}/transcribe")
        if code == 202:
            st.session_state["vivant_jusqua"] = time.monotonic() + ATTENTE_WORKER_SECONDES
            annoncer("OCR en file. Les pages déjà transcrites seront sautées.")
            st.rerun()
        st.error(f"OCR refusé (HTTP {code}) : {api_client.detail(corps)}")

    if relire.button("✎ Relire", disabled=document["status"] not in _APRES_OCR, width="stretch"):
        # Aucune page imposée : l'écran de validation ouvre la première à relire.
        st.session_state["cible_validation"] = {"document_id": document_id, "page_number": None}
        st.switch_page("page_validation.py")

    with supprimer.popover("🗑 Supprimer", width="stretch"):
        st.warning(
            "Retire le document de l'index, de la base et du disque, révisions comprises. "
            "C'est irréversible."
        )
        if st.button("Supprimer définitivement", type="primary", key=f"supprimer-{document_id}"):
            code, corps = api_client.delete(f"/documents/{document_id}")
            if code == 204:
                annoncer(f"{document['source_filename']} supprimé.")
                st.rerun()
            st.error(f"Suppression refusée (HTTP {code}) : {api_client.detail(corps)}")


afficher_annonce()
render_import()
st.divider()

st.subheader("Suivi")
documents = lire_documents()
if documents is None:
    st.stop()
if not documents:
    st.info("Aucun document pour l'instant.")
    st.stop()
st.session_state["statuts_rendus"] = _statuts(documents)

# Rafraîchissement automatique seulement quand le worker travaille : inutile de
# solliciter l'API toutes les cinq secondes pour un fonds au repos.
attente_worker = time.monotonic() < st.session_state.get("vivant_jusqua", 0.0)
if attente_worker or any(en_cours(document) for document in documents):
    _tableau_vivant()
else:
    _tableau()
    st.button("Rafraîchir")

st.divider()
render_actions(documents)
