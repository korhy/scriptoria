"""Validation humaine et indexation, sur la vraie stack.

Ce fichier fige ce qui n'était jusqu'ici vérifié qu'à la main : l'invariant des
révisions, le contenu réel de l'index, et l'idempotence de la réindexation.

Les tests unitaires ne peuvent pas l'attraper : ils remplacent Postgres et
Elasticsearch par des doubles. Une dimension de vecteur désalignée, un champ mal
nommé dans le mapping ou une écriture qui duplique au lieu d'écraser ne se
voient qu'ici.
"""

from typing import Any

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.ollama, pytest.mark.slow]


# --- L'invariant des révisions ----------------------------------------------


def test_la_revision_ocr_survit_a_la_validation(
    api: httpx.Client, document_indexe: dict[str, Any]
) -> None:
    """Le cœur du modèle de données : valider **ajoute**, n'écrase jamais."""
    detail = api.get(f"/pages/{document_indexe['page_id']}").json()
    revisions = detail["transcriptions"]

    assert len(revisions) >= 2
    ocr = revisions[0]
    assert ocr["revision"] == 1
    assert ocr["origin"] == "ocr"
    assert ocr["model_name"], "le modèle utilisé doit rester tracé"
    assert ocr["content_markdown"] == document_indexe["texte_ocr"]


def test_la_validation_cree_une_revision_humaine(
    api: httpx.Client, document_indexe: dict[str, Any]
) -> None:
    """Approuver sans corriger reste un geste daté : c'est aussi une révision."""
    detail = api.get(f"/pages/{document_indexe['page_id']}").json()
    derniere = detail["transcriptions"][-1]

    assert derniere["origin"] == "human"
    assert derniere["is_validated"] is True
    assert derniere["model_name"] is None


def test_une_correction_vide_est_refusee(
    api: httpx.Client, document_indexe: dict[str, Any]
) -> None:
    """Valider un texte vide effacerait la transcription en la marquant relue."""
    response = api.post(
        f"/pages/{document_indexe['page_id']}/corrections",
        json={"content_markdown": "   ", "validate_now": True},
    )

    assert response.status_code == 422


# --- Le contenu réel de l'index ---------------------------------------------


def test_le_document_atteint_l_etat_indexe(
    api: httpx.Client, document_indexe: dict[str, Any]
) -> None:
    statut = api.get(f"/documents/{document_indexe['document_id']}").json()["status"]

    assert statut == "indexed"


def test_le_fragment_indexe_porte_le_bon_vecteur_et_la_bonne_page(
    es: httpx.Client, document_indexe: dict[str, Any]
) -> None:
    """La dimension est vérifiée ici parce qu'un écart ne se voit nulle part ailleurs.

    Un modèle d'embedding changé sans réindexation produirait un rejet d'ES dont
    le message porte sur le mapping, pas sur la cause.
    """
    document_id = document_indexe["document_id"]
    fragment = es.get(f"/scriptoria-chunks/_doc/{document_id}:1").json()

    assert fragment["found"] is True
    source = fragment["_source"]
    assert source["document_id"] == document_id
    assert source["page_number"] == 1
    assert len(source["embedding"]) == 1024
    assert "FACTURE" in source["content"].upper()


def test_l_identifiant_du_fragment_derive_de_la_position(
    es: httpx.Client, document_indexe: dict[str, Any]
) -> None:
    """`<document>:<page>` : c'est ce qui rend l'écrasement possible."""
    document_id = document_indexe["document_id"]

    recherche = es.post(
        "/scriptoria-chunks/_search",
        json={"query": {"term": {"document_id": document_id}}, "_source": False},
    ).json()

    identifiants = [hit["_id"] for hit in recherche["hits"]["hits"]]
    assert identifiants == [f"{document_id}:1"]


def test_une_correction_ecrase_le_fragment_au_lieu_d_en_ajouter_un(
    api: httpx.Client, creer_document_indexe, attendre_indexation
) -> None:
    """Sans écrasement, deux versions de la même page répondraient en même temps.

    C'est la raison pour laquelle `chunk_id` ne dérive jamais du contenu.

    Ce test **modifie** le document sur lequel il porte : il se fabrique donc le
    sien. Muter le document de référence ferait dépendre les autres tests de
    l'ordre d'exécution.
    """
    origine = "TEXTE-INITIAL-A-CORRIGER"
    corrige = "TEXTE-CORRIGE-PAR-UN-HUMAIN"
    document = creer_document_indexe(f"{origine}\n\nFacture de test.", nom="correction.png")

    fragment = attendre_indexation(document["document_id"], origine)
    assert fragment["_id"] == f"{document['document_id']}:1"

    correction = api.post(
        f"/pages/{document['page_id']}/corrections",
        json={"content_markdown": f"{corrige}\n\nFacture relue.", "validate_now": True},
    )
    assert correction.status_code == 201, correction.text

    # Le fragment doit finir par porter le nouveau texte, **sans doublon** :
    # `attendre_fragment` exige un fragment unique pour ce document.
    fragment = attendre_indexation(document["document_id"], corrige)
    assert origine not in fragment["_source"]["content"], "l'ancienne version survit dans l'index"

    # L'historique, lui, conserve tout : c'est l'objet même du modèle.
    revisions = api.get(f"/pages/{document['page_id']}").json()["transcriptions"]
    assert [revision["revision"] for revision in revisions] == [1, 2]
    assert origine in revisions[0]["content_markdown"]


# --- Réindexation -----------------------------------------------------------


async def test_la_reindexation_est_idempotente(
    es: httpx.Client, document_indexe: dict[str, Any], base_accessible: None
) -> None:
    """`make reindex` doit pouvoir être rejoué : c'est la preuve qu'ES est jetable.

    Appelle la fonction du script plutôt que la ligne de commande : l'échec
    éventuel remonte alors avec sa cause plutôt qu'un code de sortie.
    """
    from scriptoria.scripts.reindex import reindex_all

    premier = await reindex_all()
    es.post("/scriptoria-chunks/_refresh")
    apres_premier = es.get("/scriptoria-chunks/_count").json()["count"]

    second = await reindex_all()
    es.post("/scriptoria-chunks/_refresh")
    apres_second = es.get("/scriptoria-chunks/_count").json()["count"]

    assert premier == second, "deux réindexations ne rendent pas le même compte"
    assert apres_premier == apres_second, "la réindexation duplique des fragments"
    assert apres_second >= 1


def test_la_page_de_reference_survit_a_la_reindexation(
    es: httpx.Client, document_indexe: dict[str, Any]
) -> None:
    """Reconstruite depuis Postgres, donc sans rien perdre de ce qui est validé."""
    document_id = document_indexe["document_id"]

    fragment = es.get(f"/scriptoria-chunks/_doc/{document_id}:1").json()

    assert fragment["found"] is True
    assert len(fragment["_source"]["embedding"]) == 1024
