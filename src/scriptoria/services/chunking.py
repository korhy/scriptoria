"""Découpage du Markdown validé en fragments indexables.

**Point ouvert — non tranché.** Deux granularités s'opposent :

- *Par page* : simple, aligné sur l'unité de validation, mais un fragment d'une
  page entière dilue le signal sémantique à la recherche.
- *Par section détectée dans le Markdown* (titres, paragraphes) : fragments plus
  cohérents, mais dépend de la qualité du balisage produit par l'OCR — qui est
  précisément ce dont on ne peut pas encore juger.

Trancher demande de mesurer la qualité de recherche sur des documents réels.
En attendant, le découpage reste isolé derrière cette fonction.
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


def chunk_markdown(markdown: str, document_id: UUID, page_number: int) -> list[Chunk]:
    """Découpe le Markdown validé d'une page en fragments."""
    raise NotImplementedError("Chunking — granularité à trancher puis implémenter")
