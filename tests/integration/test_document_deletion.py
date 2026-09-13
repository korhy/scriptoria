"""Suppression d'un document sur la vraie stack.

Les tests unitaires fixent l'ordre des gestes ; ils ne peuvent pas prouver que la
cascade de Postgres emporte pages, révisions et jobs, que la requête ES vise bien
le champ `document_id`, ni que les dossiers disparaissent du volume. C'est ici.

Le document supprimé est fabriqué pour l'occasion : supprimer le document de
référence partagé ferait échouer les tests exécutés après celui-ci.
"""

from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select

from scriptoria.config import get_settings
from scriptoria.db.models import Job, Page, Transcription
from scriptoria.db.session import create_engine, create_sessionmaker
from scriptoria.domain.enums import JobStatus
from scriptoria.services.storage import PREPROCESSED_ROOT, RAW_ROOT


async def lignes_en_base(document_id: str, page_id: str) -> dict[str, int]:
    """Compte ce qu'il reste en base — interrogée directement, sans passer par l'API."""
    engine = create_engine(get_settings())
    try:
        async with create_sessionmaker(engine)() as session:

            async def compter(modele: Any, condition: Any) -> int:
                return await session.scalar(
                    select(func.count()).select_from(modele).where(condition)
                )

            return {
                "pages": await compter(Page, Page.document_id == UUID(document_id)),
                "transcriptions": await compter(
                    Transcription, Transcription.page_id == UUID(page_id)
                ),
                "jobs": await compter(Job, Job.document_id == UUID(document_id)),
            }
    finally:
        await engine.dispose()


async def ajouter_job_mort(document_id: str) -> None:
    """Un job que la base croit en cours et qu'arq ne connaît pas.

    C'est l'état qu'a laissé le défaut d'annulation corrigé le 2026-09-13. L'insérer
    directement évite de rejouer une coupure d'OCR, et d'arrêter le worker.
    """
    engine = create_engine(get_settings())
    try:
        async with create_sessionmaker(engine)() as session:
            session.add(
                Job(
                    document_id=UUID(document_id),
                    kind="transcribe",
                    status=JobStatus.RUNNING,
                    arq_job_id=f"job-mort-{uuid4().hex}",
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


def fragments_indexes(es: httpx.Client, document_id: str) -> int:
    es.post("/scriptoria-chunks/_refresh")
    reponse = es.post(
        "/scriptoria-chunks/_count", json={"query": {"term": {"document_id": document_id}}}
    )
    return reponse.json()["count"]


async def test_la_suppression_ne_laisse_rien_derriere(
    api: httpx.Client,
    es: httpx.Client,
    ollama_pret: None,
    base_accessible: None,
    creer_document_indexe,
) -> None:
    document = creer_document_indexe(f"Note destinée à la suppression — {uuid4().hex}")
    document_id, page_id = document["document_id"], document["page_id"]
    dossiers = [
        get_settings().data_dir / racine / document_id for racine in (RAW_ROOT, PREPROCESSED_ROOT)
    ]

    # Sans ce constat préalable, un test qui ne trouverait rien à supprimer passerait.
    avant = await lignes_en_base(document_id, page_id)
    assert avant["pages"] == 1 and avant["transcriptions"] >= 1 and avant["jobs"] >= 1, avant
    assert fragments_indexes(es, document_id) == 1
    assert all(dossier.is_dir() for dossier in dossiers), dossiers

    reponse = api.delete(f"/documents/{document_id}")

    assert reponse.status_code == 204, reponse.text
    assert api.get(f"/documents/{document_id}").status_code == 404
    assert api.get(f"/pages/{page_id}").status_code == 404
    assert await lignes_en_base(document_id, page_id) == {
        "pages": 0,
        "transcriptions": 0,
        "jobs": 0,
    }
    assert fragments_indexes(es, document_id) == 0
    assert not any(dossier.exists() for dossier in dossiers), dossiers


def test_supprimer_deux_fois_donne_404(
    api: httpx.Client, ollama_pret: None, creer_document_indexe
) -> None:
    document = creer_document_indexe(f"Note supprimée deux fois — {uuid4().hex}")

    assert api.delete(f"/documents/{document['document_id']}").status_code == 204
    assert api.delete(f"/documents/{document['document_id']}").status_code == 404


async def test_un_job_mort_dans_arq_n_empeche_pas_la_suppression(
    api: httpx.Client, ollama_pret: None, base_accessible: None, creer_document_indexe
) -> None:
    """Interroge le vrai Redis : la base dit `running`, arq ne connaît pas le job."""
    document = creer_document_indexe(f"Note au job fantôme — {uuid4().hex}")
    await ajouter_job_mort(document["document_id"])

    reponse = api.delete(f"/documents/{document['document_id']}")

    assert reponse.status_code == 204, reponse.text
