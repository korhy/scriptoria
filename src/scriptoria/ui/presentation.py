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
