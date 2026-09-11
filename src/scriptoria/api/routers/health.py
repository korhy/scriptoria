"""Sondes de santé.

`/health/live` ne touche aucune dépendance : c'est la sonde du conteneur, elle
doit répondre même quand Postgres est en train de démarrer.

`/health` teste réellement les trois dépendances. Il ne renvoie jamais « ok » en
dur — sans quoi il ne prouverait rien.
"""

import asyncio
import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from scriptoria.api.deps import AppSettings, DbSession, EsClient, OllamaClient
from scriptoria.schemas.health import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", summary="Sonde de vivacité (aucune dépendance)")
async def live() -> dict[str, str]:
    return {"status": "alive"}


async def _check_db(session: DbSession) -> bool:
    await session.execute(text("SELECT 1"))
    return True


async def _check_es(es: EsClient) -> bool:
    await es.cluster.health()
    return True


async def _check_ollama(client: OllamaClient) -> bool:
    response = await client.get("/api/tags", timeout=10.0)
    response.raise_for_status()
    return True


@router.get("", summary="État réel des dépendances")
async def health(
    response: Response,
    session: DbSession,
    es: EsClient,
    ollama: OllamaClient,
    settings: AppSettings,
) -> HealthResponse:
    db_ok, es_ok, ollama_ok = await asyncio.gather(
        _check_db(session),
        _check_es(es),
        _check_ollama(ollama),
        return_exceptions=True,
    )

    def _ok(result: bool | BaseException, name: str) -> bool:
        if isinstance(result, BaseException):
            logger.warning("health: %s indisponible: %s", name, result)
            return False
        return result

    checks = {
        "db": _ok(db_ok, "postgres"),
        "elasticsearch": _ok(es_ok, "elasticsearch"),
        "ollama": _ok(ollama_ok, "ollama"),
    }
    healthy = all(checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(status="ok" if healthy else "degraded", app=settings.app_name, **checks)
