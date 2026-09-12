"""Indexation dans Elasticsearch.

Elasticsearch est un index **jetable** : ce qui compte ici est l'idempotence.
L'identifiant du document ES est le `chunk_id`, si bien qu'une réindexation
écrase au lieu de dupliquer — sans quoi `make reindex` gonflerait l'index à
chaque passage et ferait remonter deux fois la même page.
"""

from typing import Any
from uuid import UUID

import pytest

from scriptoria.services.chunking import Chunk
from scriptoria.services.indexing import (
    IndexingError,
    build_index_mapping,
    ensure_index,
    index_chunks,
    validate_embedding_dim,
)

DOCUMENT_ID = UUID("b522c30f-a742-4f09-a793-9b0f12bf8d23")
INDEX = "scriptoria-chunks"


class FakeIndices:
    def __init__(self, *, present: bool) -> None:
        self.present = present
        self.created: list[tuple[str, dict]] = []

    async def exists(self, index: str) -> bool:
        return self.present

    async def create(self, index: str, **body: Any) -> dict:
        self.created.append((index, body))
        return {"acknowledged": True}


class FakeEs:
    def __init__(self, *, present: bool = False, errors: bool = False) -> None:
        self.indices = FakeIndices(present=present)
        self.errors = errors
        self.bulk_calls: list[list[dict]] = []

    async def bulk(self, *, operations: list[dict], **kwargs: Any) -> dict:
        self.bulk_calls.append(operations)
        if self.errors:
            return {
                "errors": True,
                "items": [{"index": {"error": {"reason": "dimension mismatch"}}}],
            }
        return {"errors": False, "items": [{"index": {"result": "created"}}]}


def chunk(page_number: int, content: str = "contenu de page") -> Chunk:
    return Chunk(
        chunk_id=f"{DOCUMENT_ID}:{page_number}",
        document_id=DOCUMENT_ID,
        page_number=page_number,
        content=content,
    )


# --- Mapping ----------------------------------------------------------------


def test_le_mapping_declare_le_texte_et_le_vecteur_cote_a_cote() -> None:
    """Recherche hybride sans second magasin : BM25 et vecteurs dans le même document."""
    proprietes = build_index_mapping(1024)["mappings"]["properties"]

    assert proprietes["content"]["analyzer"] == "french"
    assert proprietes["embedding"]["dims"] == 1024
    assert proprietes["embedding"]["similarity"] == "cosine"


# --- Création d'index -------------------------------------------------------


async def test_l_index_est_cree_s_il_manque() -> None:
    es = FakeEs(present=False)

    await ensure_index(es, INDEX, embedding_dim=1024)

    assert len(es.indices.created) == 1
    index, corps = es.indices.created[0]
    assert index == INDEX
    assert corps["mappings"]["properties"]["embedding"]["dims"] == 1024


async def test_un_index_existant_n_est_pas_recree() -> None:
    """Idempotent : le worker appelle cette fonction à chaque document."""
    es = FakeEs(present=True)

    await ensure_index(es, INDEX, embedding_dim=1024)

    assert es.indices.created == []


# --- Indexation -------------------------------------------------------------


async def test_chaque_fragment_est_indexe_sous_son_chunk_id() -> None:
    """La clé de l'idempotence : l'identifiant ES est celui du fragment."""
    es = FakeEs()

    ecrits = await index_chunks(es, INDEX, [chunk(1)], [[0.1, 0.2]])

    assert ecrits == 1
    entete, document = es.bulk_calls[0]
    assert entete == {"index": {"_index": INDEX, "_id": f"{DOCUMENT_ID}:1"}}
    assert document["document_id"] == str(DOCUMENT_ID)
    assert document["page_number"] == 1
    assert document["content"] == "contenu de page"
    assert document["embedding"] == [0.1, 0.2]


async def test_plusieurs_fragments_partent_en_un_seul_appel() -> None:
    es = FakeEs()

    ecrits = await index_chunks(es, INDEX, [chunk(1), chunk(2)], [[0.1], [0.2]])

    assert ecrits == 2
    assert len(es.bulk_calls) == 1
    assert len(es.bulk_calls[0]) == 4  # deux en-têtes, deux documents


async def test_aucun_fragment_n_appelle_pas_elasticsearch() -> None:
    es = FakeEs()

    assert await index_chunks(es, INDEX, [], []) == 0
    assert es.bulk_calls == []


async def test_un_appariement_incoherent_est_refuse_avant_tout_appel() -> None:
    """Deux fragments, un vecteur : on indexerait le mauvais vecteur."""
    es = FakeEs()

    with pytest.raises(IndexingError):
        await index_chunks(es, INDEX, [chunk(1), chunk(2)], [[0.1]])

    assert es.bulk_calls == []


async def test_une_erreur_signalee_par_elasticsearch_est_relevee() -> None:
    """ES répond 200 avec `errors: true` : l'ignorer laisserait l'index incomplet."""
    es = FakeEs(errors=True)

    with pytest.raises(IndexingError) as erreur:
        await index_chunks(es, INDEX, [chunk(1)], [[0.1]])

    assert "dimension mismatch" in str(erreur.value)


# --- Garde-fou sur la dimension ---------------------------------------------


def test_une_dimension_inattendue_est_refusee_avec_les_deux_valeurs() -> None:
    """Sans ce contrôle, ES rejette plus tard avec un message sans rapport."""
    with pytest.raises(IndexingError) as erreur:
        validate_embedding_dim([[0.1, 0.2]], expected=1024)

    message = str(erreur.value)
    assert "1024" in message
    assert "2" in message


def test_la_bonne_dimension_passe_sans_bruit() -> None:
    validate_embedding_dim([[0.0] * 1024, [1.0] * 1024], expected=1024)


def test_aucun_vecteur_ne_pose_aucun_probleme() -> None:
    """Un document sans page indexable n'est pas une erreur."""
    validate_embedding_dim([], expected=1024)
