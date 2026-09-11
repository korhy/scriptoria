"""Réponse des sondes de santé."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """État réel des dépendances — chaque booléen provient d'un appel effectif."""

    status: str
    app: str
    db: bool
    elasticsearch: bool
    ollama: bool
