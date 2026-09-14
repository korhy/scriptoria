"""Validation image ↔ texte.

Une galerie des pages, comme un lecteur PDF : chaque vignette dit l'état de la
page et son score de confiance, un clic l'ouvre. La page ouverte s'affiche côte à
côte — image à gauche, Markdown éditable à droite, fragments douteux mis en
avant. ← et → passent d'une page à l'autre, « prochaine à relire » saute aux
pages restantes, et valider ouvre la suivante.

Valider ou corriger appelle `POST /pages/{id}/corrections`, qui crée une révision
`n+1` — l'UI n'écrase jamais rien. « Valider les pages restantes » appelle
`POST /documents/{id}/validate` avec les révisions **affichées** : si une page a
bougé entre-temps, l'API refuse tout.

**Le Markdown affiché vient d'un LLM lisant un document inconnu : c'est une
donnée.** Il est échappé avant tout rendu HTML, faute de quoi une page scannée
portant du balisage ferait exécuter n'importe quoi dans le navigateur du relecteur.
"""

import html

import api_client
import streamlit as st
from presentation import (
    FILTRES_GALERIE,
    SEUIL_ALERTE,
    STATUS_LABELS,
    avancement,
    cle_widget,
    confirmation_validation_groupee,
    decouper,
    filtrer_pages,
    index_paquet,
    index_selection,
    libelle_avancement,
    libelle_document,
    libelle_paquet,
    libelle_vignette,
    page_voisine,
    prochaine_a_relire,
    refus_validation_groupee,
    revisions_affichees,
    texte_modifie,
    vignette,
)

# Largeur demandée à l'API : le double de l'affichage, pour rester net sur un écran dense.
LARGEUR_VIGNETTE = 320
HAUTEUR_GALERIE = 820
VARIANTES = {"preprocessed": "prétraitée", "raw": "brute"}

# Clés de session partagées entre les rappels et le rendu.
EDITION = "validation_edition"
NAVIGATION_BLOQUEE = "validation_navigation_bloquee"
MESSAGE = "validation_message"


class ImageIndisponibleError(Exception):
    """Levée plutôt que rendue : `st.cache_data` garderait un échec en mémoire."""


@st.cache_data(show_spinner=False, max_entries=2000)
def charger_vignette(document_id: str, page_number: int) -> bytes:
    """En cache : l'image d'une page ne change jamais, seul son texte évolue."""
    octets = api_client.get_image(
        f"/documents/{document_id}/pages/{page_number}/thumbnail", width=LARGEUR_VIGNETTE
    )
    if octets is None:
        raise ImageIndisponibleError
    return octets


@st.cache_data(show_spinner=False, max_entries=60)
def charger_image(document_id: str, page_number: int, variante: str) -> bytes:
    """En cache aussi : revenir sur une page ne doit pas retélécharger son image."""
    octets = api_client.get_image(
        f"/documents/{document_id}/pages/{page_number}/image", variant=variante
    )
    if octets is None:
        raise ImageIndisponibleError
    return octets


# --- Navigation ------------------------------------------------------------------


def modification_en_cours() -> bool:
    edition = st.session_state.get(EDITION)
    if edition is None:
        return False
    cle, original = edition
    return texte_modifie(st.session_state.get(cle, original), original)


def aller(memoire: str, page_id: str | None) -> None:
    """Rappel de navigation : exécuté avant le rerun, donc avant que rien ne se dessine.

    Refusé tant que l'éditeur porte des modifications non enregistrées : changer
    de page les perdrait sans rien dire.
    """
    if page_id is None:
        return
    if modification_en_cours():
        st.session_state[NAVIGATION_BLOQUEE] = True
        return
    st.session_state[memoire] = page_id


def abandonner_modifications() -> None:
    edition = st.session_state.get(EDITION)
    if edition is not None:
        # Retirer la clé rend au widget sa valeur initiale : le texte de la révision.
        st.session_state.pop(edition[0], None)


# --- Galerie ---------------------------------------------------------------------


def render_vignette(document_id: str, page: dict, *, active: bool, memoire: str) -> None:
    etat = vignette(page)
    with st.container(border=True):
        try:
            st.image(charger_vignette(document_id, page["page_number"]), width="stretch")
        except ImageIndisponibleError:
            st.caption("image indisponible")
        aide = etat.libelle
        if etat.alerte:
            aide += f" — score sous le seuil de {SEUIL_ALERTE:.2f}".replace(".", ",")
        st.button(
            libelle_vignette(page),
            key=f"vignette-{page['id']}",
            type="primary" if active else "secondary",
            width="stretch",
            help=aide,
            on_click=aller,
            args=(memoire, page["id"]),
        )


def choisir_pages_affichees(document_id: str, pages: list[dict], page_id: str) -> list[dict]:
    """Filtre et paquet, sur toute la largeur : la colonne des vignettes est trop étroite."""
    colonne_filtre, colonne_paquet = st.columns([2, 5], vertical_alignment="center")
    with colonne_filtre:
        filtre = (
            st.pills(
                "Filtre",
                FILTRES_GALERIE,
                default="toutes",
                key=f"validation_filtre-{document_id}",
                label_visibility="collapsed",
            )
            or "toutes"
        )
    paquets = decouper(filtrer_pages(pages, filtre))
    if not paquets:
        return []

    choisi = index_paquet(paquets, page_id)
    if len(paquets) > 1:
        # Clé liée à la page ouverte : le paquet suit « Précédente » et « Suivante »,
        # et un paquet choisi à la main reste affiché tant qu'on ne change pas de page.
        identifiants = [page["id"] for paquet in paquets for page in paquet]
        with colonne_paquet:
            selection = st.pills(
                "Pages affichées",
                list(range(len(paquets))),
                format_func=lambda index: libelle_paquet(paquets[index]),
                default=choisi,
                key=cle_widget(f"validation_paquet-{document_id}-{filtre}-{page_id}", identifiants),
                label_visibility="collapsed",
            )
        choisi = choisi if selection is None else selection
    return paquets[choisi]


def render_galerie(document_id: str, affichees: list[dict], page_id: str, memoire: str) -> None:
    if not affichees:
        st.caption("Aucune page pour ce filtre.")
        return
    with st.container(height=HAUTEUR_GALERIE, border=False):
        for page in affichees:
            render_vignette(document_id, page, active=page["id"] == page_id, memoire=memoire)


# --- Validation groupée -------------------------------------------------------------


def render_validation_groupee(document: dict, pages: list[dict]) -> None:
    raison = refus_validation_groupee(document, pages)
    restantes = len(pages) - avancement(pages).validees
    if restantes == 0:
        libelle = "✓ Tout est validé"
    elif restantes == 1:
        libelle = "✓ Valider la page restante"
    else:
        libelle = f"✓ Valider les {restantes} pages restantes"

    with st.popover(libelle, disabled=raison is not None, help=raison, width="stretch"):
        st.warning(confirmation_validation_groupee(pages))
        bloque = modification_en_cours()
        if bloque:
            st.error(
                "La page ouverte porte des modifications non enregistrées : les enregistrer "
                "ou les abandonner d'abord — la validation groupée porte sur le texte enregistré."
            )
        confirmer = st.button(
            "Confirmer la validation groupée",
            type="primary",
            disabled=bloque,
            key=f"validation_lot-{document['id']}",
            width="stretch",
        )
    if raison is not None and restantes:
        st.caption(raison)

    if not confirmer:
        return
    code, reponse = api_client.post(
        f"/documents/{document['id']}/validate",
        {"expected_revisions": revisions_affichees(pages)},
    )
    if code != 200 or not isinstance(reponse, dict):
        st.error(f"Validation refusée (HTTP {code}) : {api_client.detail(reponse)}")
        return
    validees = len(reponse["validated_pages"])
    statut = STATUS_LABELS.get(reponse["document_status"], reponse["document_status"])
    st.session_state[MESSAGE] = (
        f"{validees} page(s) validée(s) en lot — document « {statut} », l'indexation suit."
    )
    st.rerun()


def render_entete(document: dict, pages: list[dict]) -> None:
    etat = avancement(pages)
    progression, action = st.columns([3, 1], vertical_alignment="center")
    progression.progress(
        etat.validees / etat.total if etat.total else 0.0, text=libelle_avancement(etat)
    )
    with action:
        render_validation_groupee(document, pages)


# --- Page ouverte ----------------------------------------------------------------


def render_navigation(pages: list[dict], page: dict, memoire: str) -> None:
    precedente = page_voisine(pages, page["id"], -1)
    suivante = page_voisine(pages, page["id"], +1)
    prochaine = prochaine_a_relire(pages, page["id"])
    etat = vignette(page)

    gauche, centre, droite, saut = st.columns([2, 4, 2, 3], vertical_alignment="center")
    # Streamlit affiche lui-même la touche du raccourci à côté du libellé.
    gauche.button(
        "Précédente",
        key="validation_precedente",
        disabled=precedente is None,
        on_click=aller,
        args=(memoire, precedente),
        shortcut="Left",
        help="Page précédente",
        width="stretch",
    )
    centre.markdown(
        f"**Page {page['page_number']} / {len(pages)}** · {etat.icone} {etat.libelle}"
        f" · confiance {etat.score}"
    )
    droite.button(
        "Suivante",
        key="validation_suivante",
        disabled=suivante is None,
        on_click=aller,
        args=(memoire, suivante),
        shortcut="Right",
        help="Page suivante",
        width="stretch",
    )
    saut.button(
        "⏭ Prochaine à relire",
        key="validation_prochaine",
        disabled=prochaine is None,
        on_click=aller,
        args=(memoire, prochaine),
        width="stretch",
    )


def render_fragments_douteux(transcription: dict, texte: str) -> None:
    """Met en avant les fragments que les contrôles de confiance ont signalés."""
    blocs = [
        bloc for bloc in transcription.get("confidence_blocks", []) if bloc["score"] < SEUIL_ALERTE
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
            "validée": (
                "en lot"
                if transcription["bulk_validated"]
                else ("oui" if transcription["is_validated"] else "non")
            ),
            "créée le": transcription["created_at"][:19].replace("T", " "),
        }
        for transcription in transcriptions
    ]
    st.dataframe(lignes, width="stretch", hide_index=True)


def enregistrer(page: dict, pages: list[dict], memoire: str, texte: str) -> None:
    code, reponse = api_client.post(
        f"/pages/{page['id']}/corrections",
        {"content_markdown": texte, "validate_now": True},
    )
    if code != 201 or not isinstance(reponse, dict):
        st.error(f"Enregistrement refusé (HTTP {code}) : {api_client.detail(reponse)}")
        return

    st.session_state.pop(EDITION, None)
    # La page qu'on vient de valider est exclue d'office : on passe à la suivante.
    suivante = prochaine_a_relire(pages, page["id"])
    if suivante is not None:
        st.session_state[memoire] = suivante
    st.session_state[MESSAGE] = (
        f"Page {page['page_number']} : révision {reponse['revision']} créée — "
        "la précédente est conservée."
    )
    st.rerun()


def render_page(document_id: str, page: dict, pages: list[dict], memoire: str) -> None:
    status_code, detail = api_client.get(f"/pages/{page['id']}")
    if status_code != 200 or not isinstance(detail, dict):
        st.error(f"Lecture de la page impossible (HTTP {status_code}).")
        return

    transcriptions = detail["transcriptions"]
    image, texte = st.columns([1, 1], gap="large")

    with image:
        variante = st.radio(
            "Image",
            list(VARIANTES),
            format_func=VARIANTES.get,
            horizontal=True,
            label_visibility="collapsed",
            key="validation_variante",
        )
        try:
            st.image(charger_image(document_id, page["page_number"], variante), width="stretch")
        except ImageIndisponibleError:
            st.error("Image indisponible.")

    with texte:
        if not transcriptions:
            st.info("Aucune transcription pour cette page : lancer l'OCR depuis la page Documents.")
            return

        derniere = transcriptions[-1]
        st.caption(
            f"Révision {derniere['revision']} · origine {derniere['origin']}"
            f"{' · validée en lot' if derniere['bulk_validated'] else ''}"
            f"{' · validée' if derniere['is_validated'] and not derniere['bulk_validated'] else ''}"
        )
        render_fragments_douteux(derniere, derniere["content_markdown"])

        if st.session_state.pop(NAVIGATION_BLOQUEE, False):
            st.warning(
                "Modifications non enregistrées : les enregistrer ou les abandonner "
                "avant de changer de page."
            )

        cle = f"markdown-{page['id']}-{derniere['revision']}"
        st.session_state[EDITION] = (cle, derniere["content_markdown"])
        corrige = st.text_area(
            "Markdown de la page", value=derniere["content_markdown"], height=460, key=cle
        )

        inchange = not texte_modifie(corrige, derniere["content_markdown"])
        valider, corriger, annuler = st.columns([2, 2, 1])
        if valider.button(
            "✓ Valider tel quel", type="primary", disabled=not inchange, width="stretch"
        ):
            enregistrer(page, pages, memoire, corrige)
        if corriger.button("✎ Enregistrer la correction", disabled=inchange, width="stretch"):
            enregistrer(page, pages, memoire, corrige)
        annuler.button(
            "↺",
            disabled=inchange,
            help="Abandonner les modifications",
            on_click=abandonner_modifications,
            width="stretch",
        )

        with st.expander("Historique des révisions"):
            render_historique(transcriptions)


# --- Écran ------------------------------------------------------------------------

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

# La liste déroulante porte sur des **identifiants**, jamais sur les dictionnaires
# reçus de l'API. Streamlit restaure une sélection par égalité de valeur ; or ces
# dictionnaires sont reconstruits à chaque rerun et changent dès qu'un champ
# bouge — `updated_at` change précisément au moment d'une validation. Pour la même
# raison, la clé du widget suit la liste (`cle_widget`) : sous une clé inchangée,
# le libellé affiché peut survivre à l'élément qu'il désignait. Les pages, elles,
# se choisissent par vignette, et la page ouverte se mémorise par identifiant.
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
memoire = f"validation_page-{document_id}"
if cible and cible["document_id"] == document_id:
    st.session_state[memoire] = next(
        (page["id"] for page in pages if page["page_number"] == cible["page_number"]), None
    )
if st.session_state.get(memoire) not in pages_par_id:
    # Sans page demandée, on ouvre la première qui reste à relire.
    st.session_state[memoire] = prochaine_a_relire(pages, None) or pages[0]["id"]
page = pages_par_id[st.session_state[memoire]]

if message := st.session_state.pop(MESSAGE, None):
    st.success(message)

render_entete(documents_par_id[document_id], pages)
affichees = choisir_pages_affichees(document_id, pages, page["id"])

galerie, principal = st.columns([1, 5], gap="medium")
with galerie:
    render_galerie(document_id, affichees, page["id"], memoire)
with principal:
    render_navigation(pages, page, memoire)
    render_page(document_id, page, pages, memoire)
