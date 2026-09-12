"""Découpage du Markdown validé en fragments indexables.

Granularité retenue pour commencer : **une page, un fragment**. C'est l'unité de
validation humaine, et le découpage par section dépend d'une qualité de balisage
qu'on ne saura juger que sur des documents réels dégradés.

La propriété qui compte ici est le **déterminisme de `chunk_id`** : `make reindex`
doit écraser les documents existants dans l'index, jamais les dupliquer.
"""

from uuid import UUID

from scriptoria.services.chunking import chunk_markdown

DOCUMENT_ID = UUID("b522c30f-a742-4f09-a793-9b0f12bf8d23")

PAGE = """FACTURE N° 2019-0447

| Désignation | Qté | PU HT | Total HT |
|-------------|-----|-------|----------|
| Papier A4 | 24 | 4,50 | 108,00 |
"""


def test_une_page_donne_un_fragment() -> None:
    chunks = chunk_markdown(PAGE, DOCUMENT_ID, page_number=1)

    assert len(chunks) == 1
    assert chunks[0].document_id == DOCUMENT_ID
    assert chunks[0].page_number == 1


def test_le_contenu_du_fragment_est_celui_de_la_page() -> None:
    chunk = chunk_markdown(PAGE, DOCUMENT_ID, page_number=1)[0]

    assert "FACTURE N° 2019-0447" in chunk.content
    assert "| Papier A4 | 24 | 4,50 | 108,00 |" in chunk.content


def test_l_identifiant_est_deterministe() -> None:
    """Le cœur de l'idempotence : rejouer `make reindex` ne doit rien dupliquer."""
    premier = chunk_markdown(PAGE, DOCUMENT_ID, page_number=3)[0]
    second = chunk_markdown(PAGE, DOCUMENT_ID, page_number=3)[0]

    assert premier.chunk_id == second.chunk_id


def test_l_identifiant_distingue_les_pages_et_les_documents() -> None:
    autre_document = UUID("842f0ff4-fbc1-4e3c-8504-d8460a546f93")

    page_une = chunk_markdown(PAGE, DOCUMENT_ID, page_number=1)[0]
    page_deux = chunk_markdown(PAGE, DOCUMENT_ID, page_number=2)[0]
    autre = chunk_markdown(PAGE, autre_document, page_number=1)[0]

    assert len({page_une.chunk_id, page_deux.chunk_id, autre.chunk_id}) == 3


def test_l_identifiant_ne_depend_pas_du_contenu() -> None:
    """Une correction humaine doit **remplacer** le fragment, pas en ajouter un.

    Si l'identifiant dérivait du texte, corriger une page laisserait l'ancienne
    version indexée à côté de la nouvelle — deux réponses contradictoires
    pour la même page.
    """
    avant = chunk_markdown("28,60", DOCUMENT_ID, page_number=1)[0]
    apres = chunk_markdown("28,90", DOCUMENT_ID, page_number=1)[0]

    assert avant.chunk_id == apres.chunk_id


def test_une_page_vide_ne_produit_aucun_fragment() -> None:
    """Indexer une chaîne vide polluerait la recherche avec un résultat creux."""
    assert chunk_markdown("", DOCUMENT_ID, page_number=1) == []
    assert chunk_markdown("   \n\n  ", DOCUMENT_ID, page_number=1) == []
