"""Galerie et validation groupée, sur la vraie stack.

Les tests unitaires remplacent la base par des doubles : ils ne voient ni une
migration oubliée (`bulk_validated`), ni un chargement qui ne ramènerait pas les
blocs de confiance, ni une contrainte d'unicité qui refuserait la révision `n+1`.

Le document est fabriqué ici et supprimé à la fin : une page de facture de plus
dans l'index viendrait disputer leur classement aux documents des autres tests.
"""

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.ollama, pytest.mark.slow]

# L'OCR d'une page dépasse la minute à froid.
OCR_TIMEOUT = 600.0


@pytest.fixture(scope="module")
def document_transcrit(
    api: httpx.Client, ollama_pret: None, importer, attendre
) -> Iterator[dict[str, Any]]:
    """Un document mené jusqu'à la relecture, et la galerie telle qu'elle était alors."""
    document = importer("validation-groupee.png")
    attendre(document["id"], "preprocessed")
    assert api.post(f"/documents/{document['id']}/transcribe").status_code == 202
    attendre(document["id"], "awaiting_validation", timeout=OCR_TIMEOUT)

    galerie = api.get(f"/documents/{document['id']}/pages")
    assert galerie.status_code == 200, galerie.text
    (page,) = galerie.json()
    detail = api.get(f"/pages/{page['id']}").json()

    # L'OCR laisse sa révision 1, suivie de sa mise en forme si la page en avait besoin.
    yield {"document_id": document["id"], "page": page, "revisions": detail["transcriptions"]}

    suppression = api.delete(f"/documents/{document['id']}")
    assert suppression.status_code in (204, 404), suppression.text


@pytest.fixture(scope="module")
def validation_groupee(
    api: httpx.Client, document_transcrit: dict[str, Any], attendre
) -> dict[str, Any]:
    """Une tentative périmée, puis la validation groupée de ce que la galerie affichait."""
    document_id = document_transcrit["document_id"]
    page = document_transcrit["page"]

    perimee = api.post(
        f"/documents/{document_id}/validate",
        json={"expected_revisions": {page["id"]: page["latest_revision"] + 1}},
    )
    revisions_apres_refus = len(api.get(f"/pages/{page['id']}").json()["transcriptions"])

    validation = api.post(
        f"/documents/{document_id}/validate",
        json={"expected_revisions": {page["id"]: page["latest_revision"]}},
    )
    assert validation.status_code == 200, validation.text
    attendre(document_id, "indexed")

    seconde = api.post(
        f"/documents/{document_id}/validate",
        json={"expected_revisions": {page["id"]: page["latest_revision"] + 1}},
    )
    return {
        "perimee": perimee,
        "revisions_apres_refus": revisions_apres_refus,
        "validation": validation.json(),
        "seconde": seconde,
        "detail": api.get(f"/pages/{page['id']}").json(),
    }


def test_la_galerie_decrit_une_page_sortie_de_l_ocr(document_transcrit: dict[str, Any]) -> None:
    page = document_transcrit["page"]
    revisions = document_transcrit["revisions"]

    assert page["state"] == "to_review"
    assert revisions[0]["origin"] == "ocr"
    assert {revision["origin"] for revision in revisions} <= {"ocr", "normalized"}
    assert page["latest_revision"] == revisions[-1]["revision"]
    assert page["confidence_score"] is not None
    assert page["bulk_validated"] is False


def test_la_vignette_est_un_jpeg_reduit(
    api: httpx.Client, document_transcrit: dict[str, Any]
) -> None:
    response = api.get(
        f"/documents/{document_transcrit['document_id']}/pages/1/thumbnail", params={"width": 120}
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content.startswith(b"\xff\xd8")


def test_une_validation_sur_une_revision_perimee_n_ecrit_rien(
    validation_groupee: dict[str, Any], document_transcrit: dict[str, Any]
) -> None:
    assert validation_groupee["perimee"].status_code == 409
    assert "page 1" in validation_groupee["perimee"].json()["detail"]
    assert validation_groupee["revisions_apres_refus"] == len(document_transcrit["revisions"])


def test_la_validation_groupee_ajoute_une_revision_marquee_en_lot(
    validation_groupee: dict[str, Any], document_transcrit: dict[str, Any]
) -> None:
    assert validation_groupee["validation"]["validated_pages"] == [1]
    *precedentes, validee = validation_groupee["detail"]["transcriptions"]
    affichee = precedentes[-1]

    assert precedentes == document_transcrit["revisions"], "rien ne doit être réécrit"
    assert (validee["revision"], validee["origin"]) == (affichee["revision"] + 1, "human")
    assert (validee["is_validated"], validee["bulk_validated"]) == (True, True)
    assert validee["content_markdown"] == affichee["content_markdown"]


def test_la_validation_groupee_garde_les_alertes_de_la_page(
    validation_groupee: dict[str, Any],
) -> None:
    """Les blocs relus en base, pas seulement préparés en mémoire."""
    *_, affichee, validee = validation_groupee["detail"]["transcriptions"]

    def blocs(revision: dict[str, Any]) -> list[tuple[Any, ...]]:
        return sorted(
            (b["start_offset"], b["end_offset"], b["score"], b["method"])
            for b in revision["confidence_blocks"]
        )

    assert blocs(validee) == blocs(affichee)


def test_une_seconde_validation_groupee_ne_fait_rien(validation_groupee: dict[str, Any]) -> None:
    """Document déjà indexé : ni révision de plus, ni réindexation."""
    seconde = validation_groupee["seconde"]

    assert seconde.status_code == 200, seconde.text
    assert seconde.json()["validated_pages"] == []
    assert seconde.json()["document_status"] == "indexed"
