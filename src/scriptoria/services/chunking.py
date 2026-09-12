"""Découpage du Markdown validé en fragments indexables.

**Granularité retenue le 2026-09-12 : une page, un fragment.** C'est l'unité de
validation humaine, et un identifiant lisible en découle (`<document>:<page>`).

Le découpage par section reste ouvert : il dépend de la qualité du balisage
produit par l'OCR, qu'on ne saura juger que sur des documents réels dégradés.
Le premier signal est encourageant (titres et tableaux correctement rendus sur la
fixture), mais il ne vaut pas mesure. Tant que le découpage tient dans cette
fonction, en changer n'imposera pas de toucher au reste du pipeline — seulement
de réindexer, ce que `make reindex` sait faire.
"""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Chunk:
    """Fragment indexable.

    `chunk_id` doit être déterministe pour rendre la réindexation idempotente :
    rejouer `make reindex` ne doit pas dupliquer les documents dans l'index.
    """

    chunk_id: str
    document_id: UUID
    page_number: int
    content: str


def make_chunk_id(document_id: UUID, page_number: int) -> str:
    """Identifiant stable d'un fragment.

    Dérivé de la position (document, page) et **jamais du contenu** : une
    correction humaine doit remplacer le fragment existant. Un identifiant
    dérivé du texte laisserait l'ancienne version indexée à côté de la nouvelle,
    soit deux réponses contradictoires pour la même page.
    """
    return f"{document_id}:{page_number}"


def chunk_markdown(markdown: str, document_id: UUID, page_number: int) -> list[Chunk]:
    """Découpe le Markdown validé d'une page en fragments.

    Une page sans texte ne produit rien : indexer une chaîne vide polluerait la
    recherche d'un résultat creux.
    """
    content = markdown.strip()
    if not content:
        return []

    return [
        Chunk(
            chunk_id=make_chunk_id(document_id, page_number),
            document_id=document_id,
            page_number=page_number,
            content=content,
        )
    ]
