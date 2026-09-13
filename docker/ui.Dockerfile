# UI de validation (Streamlit). Ne partage aucun code avec l'API : elle ne
# communique qu'en HTTP, ce qui la rend remplaçable par un front React sans
# toucher au reste de la stack.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --only-group ui --no-install-project

COPY src/scriptoria/ui/ ./src/scriptoria/ui/

# Streamlit lit `.streamlit/config.toml` dans le répertoire courant : c'est là
# qu'est désactivé l'envoi de statistiques d'usage à un service tiers.
WORKDIR /app/src/scriptoria/ui

EXPOSE 8501
CMD ["streamlit", "run", "streamlit_app.py", \
     "--server.address=0.0.0.0", "--server.port=8501"]
