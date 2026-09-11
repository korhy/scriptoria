"""Configuration centralisée, entièrement pilotée par variables d'environnement.

Aucune valeur n'est codée en dur ailleurs dans l'application : tout passe par
`get_settings()`. Les valeurs par défaut correspondent au réseau Docker Compose.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # `.env` porte aussi des variables destinées à Docker (POSTGRES_USER,
        # ES_JAVA_OPTS...) qui ne sont pas des champs applicatifs.
        extra="ignore",
    )

    app_name: str = "scriptoria"
    log_level: str = "INFO"
    data_dir: Path = Path("/data")

    # --- Postgres : source de vérité ---------------------------------------
    database_url: str = "postgresql+asyncpg://scriptoria:scriptoria@postgres:5432/scriptoria"

    # --- Redis : broker du worker ------------------------------------------
    redis_url: str = "redis://redis:6379/0"

    # --- Elasticsearch : index reconstructible ------------------------------
    elasticsearch_url: str = "http://elasticsearch:9200"
    elasticsearch_index: str = "scriptoria-chunks"

    # --- Ollama : sur l'hôte, hors Docker (pas de GPU Metal en conteneur) ---
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_vision_model: str = "qwen2.5vl:7b"
    ollama_embedding_model: str = "bge-m3"
    ollama_generation_model: str = "mistral:latest"
    # L'OCR vision d'une page peut dépasser la minute sur un M4.
    ollama_timeout_seconds: float = 300.0
    # Doit rester aligné avec le mapping dense_vector de l'index.
    embedding_dim: int = 1024

    api_base_url: str = "http://api:8000"

    @property
    def inbox_dir(self) -> Path:
        """Dépôt des documents entrants, avant traitement."""
        return self.data_dir / "inbox"

    @property
    def images_dir(self) -> Path:
        """Images de page : originales et prétraitées."""
        return self.data_dir / "images"

    @property
    def markdown_dir(self) -> Path:
        """Markdown validé, exporté depuis Postgres."""
        return self.data_dir / "markdown"


@lru_cache
def get_settings() -> Settings:
    """Instance unique — mise en cache pour éviter de relire `.env` à chaque appel."""
    return Settings()
