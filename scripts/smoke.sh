#!/usr/bin/env bash
# Vérifications de bout en bout de la stack.
#
# Chaque étape produit une preuve observable. Aucune ne suppose qu'un service
# fonctionne : elle l'interroge.
set -uo pipefail

API="${API:-http://localhost:8000}"
ES="${ES:-http://localhost:9200}"
OLLAMA="${OLLAMA:-http://localhost:11434}"
UI="${UI:-http://localhost:8501}"

failures=0
ok()    { printf '  \033[32m✓\033[0m %s\n' "$1"; }
ko()    { printf '  \033[31m✗\033[0m %s\n' "$1"; failures=$((failures + 1)); }
title() { printf '\n\033[1m%s\033[0m\n' "$1"; }

title "1. Elasticsearch"
es_status=$(curl -s --max-time 10 "$ES/_cluster/health" | sed -n 's/.*"status":"\([^"]*\)".*/\1/p')
case "$es_status" in
  green|yellow) ok "cluster $es_status" ;;
  *)            ko "cluster injoignable ou rouge (reçu: '${es_status:-rien}')" ;;
esac

title "2. API"
if curl -sf --max-time 10 "$API/health/live" >/dev/null; then
  ok "sonde de vivacité"
else
  ko "l'API ne répond pas sur $API"
fi

health=$(curl -s --max-time 30 "$API/health")
for dep in db elasticsearch ollama; do
  if printf '%s' "$health" | grep -q "\"$dep\":true"; then
    ok "$dep joignable depuis l'API"
  else
    ko "$dep NON joignable depuis l'API"
  fi
done

title "3. Accès base via l'API"
if curl -sf --max-time 10 "$API/documents" >/dev/null; then
  ok "GET /documents (chaîne HTTP → SQLAlchemy → Postgres)"
else
  ko "GET /documents échoue"
fi

title "4. Modèles Ollama"
tags=$(curl -s --max-time 10 "$OLLAMA/api/tags")
for model in qwen2.5vl bge-m3; do
  if printf '%s' "$tags" | grep -q "$model"; then
    ok "$model présent"
  else
    ko "$model absent — lancer 'make models'"
  fi
done

title "5. Inférence vision (preuve que l'OCR est viable sur cette machine)"
if printf '%s' "$tags" | grep -q "qwen2.5vl"; then
  # Image PNG 8x8 unie, encodée en base64 — aucune donnée réelle nécessaire.
  img="iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAAFklEQVR4nGNgGAWjYBSMglEwCkYBAAAFEAABLjTlUwAAAABJRU5ErkJggg=="
  start=$(date +%s)
  body=$(printf '{"model":"qwen2.5vl:7b","prompt":"Describe this image in one word.","images":["%s"],"stream":false}' "$img")
  resp=$(curl -s --max-time 600 "$OLLAMA/api/generate" -d "$body")
  elapsed=$(( $(date +%s) - start ))
  if printf '%s' "$resp" | grep -q '"response"'; then
    ok "inférence vision aboutie en ${elapsed}s"
    echo "     → mesure de référence pour estimer le coût par page"
  else
    ko "inférence vision échouée: $(printf '%s' "$resp" | head -c 200)"
  fi
else
  ko "modèle vision absent, inférence non testée"
fi

title "6. UI"
if curl -sf --max-time 10 "$UI" >/dev/null; then
  ok "Streamlit répond"
else
  ko "Streamlit ne répond pas sur $UI"
fi

printf '\n'
if [ "$failures" -eq 0 ]; then
  printf '\033[32mTout est vert.\033[0m\n'
else
  printf '\033[31m%d vérification(s) en échec.\033[0m\n' "$failures"
fi
exit "$failures"
