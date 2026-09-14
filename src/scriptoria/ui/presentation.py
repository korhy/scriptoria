"""Logique de présentation de l'UI.

N'importe ni Streamlit ni httpx, ni rien de `scriptoria` : l'image `ui` ne copie
que ce répertoire, et ce module est chargé à plat par les pages Streamlit comme
par les tests (`scriptoria.ui.presentation`). Ce qui s'affiche se décide ici,
où les tests peuvent le fixer.
"""

import hashlib
import re
from dataclasses import dataclass

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

# Statuts où le worker travaille encore : l'écran doit se rafraîchir seul.
_EN_COURS = frozenset({"new", "preprocessing", "transcribing"})
# Statuts qui ont franchi l'OCR, qu'il ait eu lieu ou que le texte ait été saisi.
_APRES_OCR = frozenset({"awaiting_validation", "validated", "indexed"})

_CHIFFRES = re.compile(r"(\d+)")


@dataclass(frozen=True)
class Progression:
    fraction: float
    libelle: str


def cle_naturelle(nom: str) -> tuple[str | int, ...]:
    """Clé de tri qui range `page2` avant `page10`.

    L'ordre d'import fixe les numéros de page : un tri alphabétique ferait de la
    dixième page la deuxième, sans que rien ne le signale.
    """
    morceaux = _CHIFFRES.split(nom.lower())
    # `split` alterne texte et chiffres : les types restent alignés d'un nom à l'autre.
    return tuple(int(morceau) if index % 2 else morceau for index, morceau in enumerate(morceaux))


def _statut(document: dict) -> str:
    return STATUS_LABELS.get(document["status"], document["status"])


def progression(document: dict) -> Progression:
    """Où en est un document, lisible d'un coup d'œil."""
    statut = document["status"]
    total = document["page_count"]
    faites = document.get("pages_transcribed", 0)
    fraction = faites / total if total else 0.0

    if statut in _APRES_OCR:
        return Progression(1.0, _statut(document))
    if statut == "transcribing":
        return Progression(fraction, f"OCR : {faites} / {total} pages")
    if statut == "failed" and faites:
        return Progression(fraction, f"en échec — {faites} / {total} pages déjà transcrites")
    return Progression(0.0, _statut(document))


def en_cours(document: dict) -> bool:
    return document["status"] in _EN_COURS


def action_ocr(document: dict) -> str | None:
    """`lancer`, `relancer` ou rien.

    Pour un document en échec, l'UI propose la relance sans savoir quelle étape a
    échoué : l'API refuse (409) si ce n'est pas l'OCR, et son message l'explique.
    """
    if document["status"] == "preprocessed":
        return "lancer"
    if document["status"] == "failed":
        return "relancer"
    return None


def libelle_document(document: dict) -> str:
    """Libellé d'un document dans une liste déroulante — unique, pas seulement lisible.

    Le nom de fichier ne suffit pas : un fonds compte vite des dizaines de
    `scan.png`. Vu le 2026-09-13, treize « correction.png — 1 p. — indexé »
    identiques empêchaient de savoir sur lequel « Supprimer » allait agir. La date
    d'import et un identifiant court les départagent.
    """
    importe_le = document["created_at"][:16].replace("T", " ")
    return (
        f"{document['source_filename']} — {document['page_count']} p. — "
        f"{progression(document).libelle} — {importe_le} · {document['id'][:8]}"
    )


def libelle_source(hit: dict, documents_par_id: dict[str, dict]) -> str:
    """Fichier et page d'un passage : ce qui permet d'aller vérifier la réponse."""
    document = documents_par_id.get(hit["document_id"])
    nom = document["source_filename"] if document else f"document {hit['document_id'][:8]}"
    return f"{nom} · page {hit['page_number']}"


def index_selection(options: list[str], voulu: str | None) -> int:
    """Position de l'identifiant voulu, ou 0 s'il n'est plus dans la liste."""
    return options.index(voulu) if voulu in options else 0


def cle_widget(prefixe: str, options: list[str]) -> str:
    """Clé de widget qui change dès que la liste des options change.

    Sous une clé inchangée, Streamlit recalcule la valeur côté serveur quand les
    options changent, mais le navigateur garde l'ancien libellé. Vu le 2026-09-13 :
    après une suppression, la liste affichait le document supprimé pendant que le
    panneau agissait sur un autre. Une clé neuve force un widget neuf, cohérent.
    """
    empreinte = hashlib.sha256("\n".join(options).encode()).hexdigest()[:12]
    return f"{prefixe}-{empreinte}"


# --- Galerie de validation ---------------------------------------------------------
#
# Les pages manipulées ici sont celles de `GET /documents/{id}/pages` : état,
# score de la dernière révision, validation en lot.

# Aligné sur CONFIDENCE_SECOND_PASS_THRESHOLD : en dessous, la page mérite l'œil.
SEUIL_ALERTE = 0.5
# Vignettes affichées à la fois : 200 images d'un coup alourdiraient chaque clic.
TAILLE_PAQUET = 24

_ICONES = {
    "untranscribed": ("⬜", "non transcrite"),
    "to_review": ("👁️", "à relire"),
    "draft": ("✏️", "brouillon"),
    "validated": ("✅", "validée"),
}
_VALIDEE_EN_LOT = ("☑️", "validée en lot")
_ICONE_ALERTE = "⚠️"
# Statuts où chaque page porte une lecture à approuver, ou l'a déjà été — ceux
# que l'API accepte pour la validation groupée.
_STATUTS_VALIDABLES = frozenset({"awaiting_validation", "validated", "indexed"})


@dataclass(frozen=True)
class Vignette:
    icone: str
    libelle: str
    score: str
    alerte: bool


@dataclass(frozen=True)
class Avancement:
    validees: int
    total: int
    # Numéros des pages dont le score est sous le seuil, validées en lot comprises.
    alertes: tuple[int, ...]


def _validee(page: dict) -> bool:
    return page["state"] == "validated"


def _alerte(page: dict) -> bool:
    score = page["confidence_score"]
    return score is not None and score < SEUIL_ALERTE


def _pluriel(nombre: int, mot: str) -> str:
    return f"{nombre} {mot}{'s' if nombre > 1 else ''}"


def _numeros(numeros: list[int] | tuple[int, ...]) -> str:
    return "p. " + ", ".join(str(numero) for numero in numeros)


def format_score(score: float | None) -> str:
    return "—" if score is None else f"{score:.2f}".replace(".", ",")


def vignette(page: dict) -> Vignette:
    """Ce que dit la vignette d'une page.

    L'alerte prend la place de l'icône d'état, **validée en lot comprise** : une
    page douteuse approuvée sans être lue est précisément celle qu'il faudra
    rouvrir. Le libellé, lui, garde l'état.
    """
    if _validee(page) and page["bulk_validated"]:
        icone, libelle = _VALIDEE_EN_LOT
    else:
        icone, libelle = _ICONES.get(page["state"], ("❔", page["state"]))
    alerte = _alerte(page)
    return Vignette(
        icone=_ICONE_ALERTE if alerte else icone,
        libelle=libelle,
        score=format_score(page["confidence_score"]),
        alerte=alerte,
    )


def libelle_vignette(page: dict) -> str:
    etat = vignette(page)
    return f"{etat.icone} p. {page['page_number']} · {etat.score}"


_FILTRES = {
    "toutes": lambda page: True,
    "à relire": lambda page: not _validee(page),
    "alertes": _alerte,
    "validées": _validee,
}
FILTRES_GALERIE = tuple(_FILTRES)


def filtrer_pages(pages: list[dict], filtre: str) -> list[dict]:
    if filtre not in _FILTRES:
        raise ValueError(f"filtre de galerie inconnu : {filtre!r}")
    return [page for page in pages if _FILTRES[filtre](page)]


def _position(pages: list[dict], page_id: str | None) -> int | None:
    return next((index for index, page in enumerate(pages) if page["id"] == page_id), None)


def page_voisine(pages: list[dict], page_id: str, pas: int) -> str | None:
    """Identifiant de la page `pas` rangs plus loin, ou `None` au bord de la liste."""
    position = _position(pages, page_id)
    if position is None:
        return None
    cible = position + pas
    return pages[cible]["id"] if 0 <= cible < len(pages) else None


def prochaine_a_relire(pages: list[dict], page_id: str | None) -> str | None:
    """Première page non validée après la page ouverte, en reprenant au début.

    Jamais la page ouverte elle-même : « prochaine » veut dire ailleurs. Une page
    validée en lot n'est plus à relire — son alerte, elle, reste visible.
    """
    position = _position(pages, page_id)
    ordre = pages if position is None else pages[position + 1 :] + pages[:position]
    return next((page["id"] for page in ordre if not _validee(page)), None)


def avancement(pages: list[dict]) -> Avancement:
    return Avancement(
        validees=sum(1 for page in pages if _validee(page)),
        total=len(pages),
        alertes=tuple(page["page_number"] for page in pages if _alerte(page)),
    )


def libelle_avancement(etat: Avancement) -> str:
    pages = "pages validées" if etat.total > 1 else "page validée"
    texte = f"{etat.validees} / {etat.total} {pages}"
    if etat.alertes:
        texte += f" · {_pluriel(len(etat.alertes), 'alerte')} ({_numeros(etat.alertes)})"
    return texte


def refus_validation_groupee(document: dict, pages: list[dict]) -> str | None:
    """Pourquoi le bouton « tout valider » est inactif — `None` s'il peut servir.

    Mêmes règles que l'API, qui reste seule juge : l'UI ne fait qu'éviter un clic
    voué au refus, et dire pourquoi.
    """
    restantes = [page for page in pages if not _validee(page)]
    if not restantes:
        return "Toutes les pages sont déjà validées."
    if document["status"] not in _STATUTS_VALIDABLES:
        return (
            f"Document « {_statut(document)} » : la validation groupée attend la fin "
            "de la transcription."
        )
    non_transcrites = [
        page["page_number"] for page in restantes if page["state"] == "untranscribed"
    ]
    if non_transcrites:
        return (
            f"Pages sans transcription ({_numeros(non_transcrites)}) : lancer l'OCR ou "
            "saisir leur texte d'abord."
        )
    return None


def confirmation_validation_groupee(pages: list[dict]) -> str:
    """Ce que le relecteur approuve en cliquant — pages douteuses nommées."""
    restantes = [page for page in pages if not _validee(page)]
    texte = f"Valider {_pluriel(len(restantes), 'page')} sans les relire une à une."
    alertes = [page["page_number"] for page in restantes if _alerte(page)]
    if alertes:
        verbe = "porte" if len(alertes) == 1 else "portent"
        texte += f" Parmi elles, {len(alertes)} {verbe} une alerte : {_numeros(alertes)}."
    return (
        texte + " Elles resteront marquées « validée en lot » et ne serviront pas de "
        "référence pour mesurer l'OCR."
    )


def revisions_affichees(pages: list[dict]) -> dict[str, int]:
    """La dernière révision de chaque page, telle que la galerie l'affiche (0 sans texte)."""
    return {page["id"]: page["latest_revision"] or 0 for page in pages}


def texte_modifie(saisi: str, original: str) -> bool:
    return saisi.strip() != original.strip()


def decouper(pages: list[dict], taille: int = TAILLE_PAQUET) -> list[list[dict]]:
    return [pages[debut : debut + taille] for debut in range(0, len(pages), taille)]


def index_paquet(paquets: list[list[dict]], page_id: str | None) -> int:
    """Le paquet qui contient la page ouverte, le premier sinon."""
    return next(
        (index for index, paquet in enumerate(paquets) if _position(paquet, page_id) is not None),
        0,
    )


def libelle_paquet(paquet: list[dict]) -> str:
    return f"p. {paquet[0]['page_number']}-{paquet[-1]['page_number']}"
