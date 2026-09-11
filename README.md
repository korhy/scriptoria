# Scriptoria

Numérisation de documents papier et RAG documentaire, **100 % local** — aucun appel API externe.

```
Image (scan) → Prétraitement → LLM vision (OCR) → Score de confiance
   → Validation/correction utilisateur → Markdown validé
   → Chunking → Embeddings → Elasticsearch → Interrogation RAG
```

## Prérequis

- Docker Desktop
- [Ollama](https://ollama.com/download) **installé sur l'hôte, pas dans Docker**

> **Pourquoi Ollama hors Docker ?** Docker Desktop sur macOS n'expose pas le GPU Metal
> aux conteneurs. Un Ollama conteneurisé tournerait en CPU pur — 5 à 10× plus lent,
> inexploitable pour de l'OCR vision page par page. Les conteneurs le joignent via
> `host.docker.internal:11434`.

## Démarrage

```bash
ollama serve              # dans un terminal dédié
make check-host           # vérifie Ollama et l'espace disque
make models               # ~7 Go, une seule fois
make up                   # build + démarrage de la stack
make migrate              # crée le schéma Postgres
make smoke                # prouve que tout répond, Ollama vision compris
```

| Service | URL |
|---|---|
| API (OpenAPI) | http://localhost:8000/docs |
| UI de validation | http://localhost:8501 |
| Elasticsearch | http://localhost:9200 |
| Kibana (profil `debug`) | http://localhost:5601 |

Kibana n'est pas démarré par défaut : `docker compose --profile debug up -d kibana`.

## Architecture

| Brique | Choix | Où |
|---|---|---|
| API | FastAPI | conteneur `api` |
| Worker OCR | arq + Redis | conteneur `worker` |
| État métier | PostgreSQL 16 | conteneur `postgres` |
| Index de recherche | Elasticsearch 9 | conteneur `elasticsearch` |
| UI de validation | Streamlit | conteneur `ui` |
| LLM vision / embeddings / génération | Ollama | **hôte** |

**Postgres est la source de vérité. Elasticsearch est un index jetable** : `make reindex`
doit toujours pouvoir le reconstruire intégralement depuis Postgres.

**Les corrections ne sont jamais destructives** : chaque validation humaine crée une
révision `n+1` dans `transcriptions`. L'historique image → texte brut → texte corrigé
reste intégralement consultable.

## Modèles

| Usage | Modèle | Poids |
|---|---|---|
| OCR vision | `qwen2.5vl:7b` | ~6 Go |
| Embeddings | `bge-m3` (1024 dim) | ~1,2 Go |
| Génération RAG | `mistral:latest` | ~4,4 Go |

Sur une machine à 16 Go unifiés, **ne jamais garder deux modèles chargés simultanément** :
`export OLLAMA_MAX_LOADED_MODELS=1` avant `ollama serve`.

## État du projet

Le squelette tourne ; le pipeline n'est pas implémenté. Les modules de
`src/scriptoria/services/` sont des stubs typés qui fixent les frontières sans
préjuger de l'implémentation.

Deux points restent ouverts (voir `CLAUDE.md`) : la méthode définitive de calcul
de la confiance et la granularité du chunking.

## Commandes

`make help` liste tout.
