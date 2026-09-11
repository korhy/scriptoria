# Image unique pour `api` et `worker`.
#
# Volontairement mono-étage et dev-complet : `make test`, `make lint` et
# `make migrate` s'exécutent DANS ce conteneur. Un étage runtime allégé sans
# pytest/ruff/alembic casserait ces trois commandes.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# libglib2.0-0 : requis par opencv-python-headless malgré l'absence de GUI.
# curl : vérifications manuelles depuis le conteneur.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Couche de dépendances séparée : invalidée seulement si pyproject/uv.lock changent.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

COPY src/ ./src/
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen

EXPOSE 8000
CMD ["uvicorn", "scriptoria.main:app", "--host", "0.0.0.0", "--port", "8000"]
