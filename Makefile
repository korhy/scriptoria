.DEFAULT_GOAL := help
SHELL := /bin/bash
COMPOSE := docker compose

# Modèles Ollama. Tournent sur l'HÔTE, pas dans Docker (pas de GPU Metal en conteneur).
VISION_MODEL     ?= qwen2.5vl:7b
EMBEDDING_MODEL  ?= bge-m3

.PHONY: help up down logs ps models migrate revision test lint fmt shell psql reindex smoke reset check-host

help: ## Affiche cette aide
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.env:
	@cp .env.example .env && echo "→ .env créé depuis .env.example"

check-host: ## Vérifie les prérequis hôte (Ollama, disque)
	@command -v ollama >/dev/null || { echo "✗ ollama absent — https://ollama.com/download"; exit 1; }
	@curl -sf http://localhost:11434/api/tags >/dev/null \
		|| { echo "✗ Ollama ne répond pas sur :11434 — lancer 'ollama serve'"; exit 1; }
	@echo "✓ Ollama joignable"
	@df -h / | awk 'NR==2 {print "  disque libre: " $$4}'

up: .env ## Démarre la stack (build si nécessaire)
	$(COMPOSE) up -d --build
	@echo "→ API      http://localhost:8000/docs"
	@echo "→ UI       http://localhost:8501"
	@echo "→ ES       http://localhost:9200"

down: ## Arrête la stack (les volumes sont conservés)
	$(COMPOSE) down

logs: ## Suit les logs (make logs S=api pour un seul service)
	$(COMPOSE) logs -f $(S)

ps: ## État des services
	$(COMPOSE) ps

models: ## Télécharge les modèles Ollama (~7 Go, une seule fois)
	@echo "→ pull $(VISION_MODEL) puis $(EMBEDDING_MODEL)"
	ollama pull $(VISION_MODEL)
	ollama pull $(EMBEDDING_MODEL)

migrate: ## Applique les migrations Alembic
	$(COMPOSE) exec api alembic upgrade head

revision: ## Génère une migration (make revision M="ajout table x")
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(M)"

test: ## Lance la suite de tests avec couverture (intégration comprise, ~5 min)
	$(COMPOSE) exec api pytest --cov --cov-report=term-missing

test-unit: ## Tests unitaires seuls — rapides, sans Ollama ni stack peuplée
	$(COMPOSE) exec api pytest tests/unit --cov --cov-report=term-missing

test-integration: ## Tests d'intégration seuls (stack démarrée + Ollama requis)
	$(COMPOSE) exec api pytest tests/integration -q

lint: ## Vérifie le style sans rien modifier
	$(COMPOSE) exec api ruff check src tests
	$(COMPOSE) exec api ruff format --check src tests

fmt: ## Formate et corrige automatiquement
	$(COMPOSE) exec api ruff format src tests
	$(COMPOSE) exec api ruff check --fix src tests

shell: ## Shell dans le conteneur api
	$(COMPOSE) exec api bash

psql: ## Console Postgres
	$(COMPOSE) exec postgres psql -U scriptoria -d scriptoria

reindex: ## Reconstruit l'index Elasticsearch depuis Postgres (ES est jetable)
	$(COMPOSE) exec api python -m scriptoria.scripts.reindex

normalize: ## Met en forme un document déjà transcrit, sans relancer l'OCR (DOCUMENT=id)
	$(if $(DOCUMENT),,$(error DOCUMENT=<id> requis, ex. make normalize DOCUMENT=<uuid du document>))
	$(COMPOSE) exec api python -m scriptoria.scripts.normalize $(DOCUMENT)

eval: ## Évalue OCR et recherche sur un corpus réel (CORPUS=nom, DOCUMENT=id pour reprendre)
	$(if $(CORPUS),,$(error CORPUS=<nom> requis, ex. make eval CORPUS=reglement-valmy-1953))
	$(COMPOSE) exec api python -m scriptoria.scripts.evaluate $(CORPUS) $(if $(DOCUMENT),--document $(DOCUMENT))

smoke: ## Vérifications de bout en bout (infra + Ollama + vision)
	@bash scripts/smoke.sh

reset: ## DESTRUCTIF — supprime conteneurs ET volumes (Postgres + ES vidés)
	@read -p "Supprimer volumes Postgres et Elasticsearch ? [y/N] " a; [ "$$a" = "y" ]
	$(COMPOSE) down -v
