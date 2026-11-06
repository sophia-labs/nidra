#!/usr/bin/env bash
# Nidra in one container (Dockerfile.nidra): gardend, the engine's ingestion service and its API, supervised.
#
#   nidra            (the default) run all three until one stops or the container is told to
#   nidra url        print the link to open (the key is in it)
#   nidra <command>  run something else, e.g. `nidra gardend token create --label me --grant member`
#
# On first start it makes a key (kept in /data/nidra.key), copies the Chinook sample to /data/chinook.sqlite
# and creates graph `nidra`, which holds the catalog, the glossary and the vector index (gardend's `find`,
# refreshed every minute). Everything that lasts is under /data.
#
# The model, from the simplest setting: OPENROUTER_API_KEY alone means OpenRouter with Claude Sonnet (Haiku
# for the small calls); NIDRA_MODEL picks another model; DEFAULT_MODELS_ENDPOINT/_API_KEY/_MODEL name any
# OpenAI-compatible endpoint instead.
set -euo pipefail

DATA=/data
PROFILE=$DATA/profile
GRAPH=${NIDRA_GRAPH_ID:-nidra}
EVERY=${NIDRA_INDEX_EVERY_SECONDS:-60}
OPEN_URL=${NIDRA_URL:-http://localhost:8086}
GARDEND_PORT=8086
say() { printf 'nidra: %s\n' "$*"; }

link() { printf '%s/nidra#key=%s\n' "${OPEN_URL%/}" "$(cat "$DATA/nidra.key")"; }
case "${1:-}" in
  "") ;;
  url) # the supervisor makes the key; wait for it rather than make another
    for _ in $(seq 1 30); do [ -s "$DATA/nidra.key" ] && { link; exit 0; }; sleep 1; done
    echo "nidra: no key yet in $DATA (is Nidra running?)" >&2; exit 1 ;;
  *) exec "$@" ;;
esac

umask 077
mkdir -p "$DATA"
if [ ! -s "$DATA/nidra.key" ]; then
  head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' >"$DATA/nidra.key.new" && mv "$DATA/nidra.key.new" "$DATA/nidra.key"
  say "made a key ($DATA/nidra.key)"
fi
KEY=$(cat "$DATA/nidra.key")

# The model.
if [ -n "${OPENROUTER_API_KEY:-}" ]; then
  : "${DEFAULT_MODELS_API_KEY:=$OPENROUTER_API_KEY}"
  : "${DEFAULT_MODELS_ENDPOINT:=https://openrouter.ai/api/v1}"
  : "${DEFAULT_MODELS_MODEL:=anthropic/claude-sonnet-5.5}"
  case "$DEFAULT_MODELS_ENDPOINT" in https://openrouter.ai/*) : "${NON_REASONING_MODEL:=anthropic/claude-haiku-5.5}" ;; esac
fi
[ -z "${NIDRA_MODEL:-}" ] || DEFAULT_MODELS_MODEL=$NIDRA_MODEL
[ -n "${DEFAULT_MODELS_API_KEY:-}" ] || say "WARNING: no model key. Run Nidra with -e OPENROUTER_API_KEY=sk-or-... to ask questions."
export DEFAULT_MODELS_API_KEY="${DEFAULT_MODELS_API_KEY:-}" DEFAULT_MODELS_ENDPOINT="${DEFAULT_MODELS_ENDPOINT:-}" \
  DEFAULT_MODELS_MODEL="${DEFAULT_MODELS_MODEL:-}"
[ -z "${NON_REASONING_MODEL:-}" ] || export NON_REASONING_MODEL

# The engine's settings: its store is gardend, here; there is no Postgres and no embedding endpoint. Writes go
# through this gardend's Emporium ledger (NIDRA_WRITES=commit; -e NIDRA_WRITES=sparql is the opt-out). A volume
# written by an older Nidra over SPARQL is adopted by the ledger when the engine's API starts; the ingestion
# service, started first, waits for that (NIDRA_ADOPT_WAIT_S, default 900 s) before it writes.
export NIDRA_WRITES="${NIDRA_WRITES:-commit}"
export CATALOG_READ_BACKEND=rdf AO_RDF_STRICT=1 GARDEND_URL=http://127.0.0.1:$GARDEND_PORT GARDEND_TOKEN="$KEY" \
  GARDEND_GRAPH_ID="$GRAPH" NIDRA_SCHEDULER=internal AO_FLAG_SEMANTIC_COMPILATION_ENABLED=true \
  CONNECTION_STRINGS="${CONNECTION_STRINGS:-sqlite:////data/chinook.sqlite}" \
  INGESTION_SERVICE_URL=http://127.0.0.1:3002 \
  EMBED_ENDPOINT="${DEFAULT_MODELS_ENDPOINT:-}" EMBED_MODEL=unused-on-gardend RERANK_ENDPOINT="" \
  POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=1 POSTGRES_USER=nidra POSTGRES_PASSWORD=no-postgres POSTGRES_DATABASE=none \
  LLM_MAX_INFLIGHT="${NIDRA_LLM_MAX_INFLIGHT:-2}"
# What gardend passes on to the Nidra service it supervises (its own GARDEN_* it keeps back).
export NIDRA_GARDEND_RDF_TOKEN="$KEY" NIDRA_GRAPH_ID="$GRAPH"

[ -f "$DATA/chinook.sqlite" ] || { cp /opt/nidra/chinook.sqlite "$DATA/chinook.sqlite"; say "copied the Chinook sample to $DATA/chinook.sqlite"; }

PIDS=()    # gardend, the ingestion service, the engine: when one stops, all stop
HELPERS=() # the index refresher and the announcer
stop_all() {
  kill -TERM "${HELPERS[@]}" "${PIDS[@]}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap 'say "stopping"; trap - TERM INT; stop_all; exit 0' TERM INT

# 1. gardend: the graph, `find`, the UI and the Nidra service behind it.
UI=()
if [ -f /opt/nidra/ui/index.html ]; then UI=(GARDEN_UI_DIR=/opt/nidra/ui); else say "this image has no UI; the APIs answer on :8086 and :3001"; fi
env GARDEN_LOOPBACK_TOKEN="$KEY" GARDEN_LOOPBACK_HOST=0.0.0.0 GARDEN_SERVICES_DIR=/opt/nidra/services "${UI[@]}" \
  /usr/local/bin/gardend serve --profile "$PROFILE" --port "$GARDEND_PORT" &
GARDEND=$!; PIDS+=("$GARDEND")
for _ in $(seq 1 120); do
  curl -sf -m 3 "http://127.0.0.1:$GARDEND_PORT/health" >/dev/null 2>&1 && break
  kill -0 "$GARDEND" 2>/dev/null || { say "gardend stopped while starting"; exit 1; }
  sleep 1
done
api() { curl -sS -m 20 -H "Authorization: Bearer $KEY" -H 'content-type: application/json' "$@"; }
if ! api -X POST "http://127.0.0.1:$GARDEND_PORT/api/sparql/query" -d "{\"graphId\":\"$GRAPH\",\"sparql\":\"ASK { }\"}" 2>/dev/null | grep -q '"resultType"'; then
  api -X POST "http://127.0.0.1:$GARDEND_PORT/api/graphs" -d "{\"graphId\":\"$GRAPH\",\"title\":\"Nidra catalog\"}" >/dev/null && say "created graph $GRAPH"
fi
# `find`: on, built once (the model is in the image; nothing is downloaded), then refreshed.
( while kill -0 "$GARDEND" 2>/dev/null; do
    /usr/local/bin/gardend index --graph "$GRAPH" --profile "$PROFILE" 2>&1 | grep -E '^entity index|failed' | grep -v '(0 embedded,' || true
    sleep "$EVERY"
  done ) &
HELPERS+=($!)

# 2. the ingestion service (catalogues the sources, then compiles the semantic layer), 3. the engine's API (it
# alone adopts a SPARQL-written catalog into the ledger, at its start; the ingestion service waits for it).
cd /app
python -m auto_ontology.ingestion_service &
PIDS+=($!)
python -m auto_ontology.server &
ENGINE=$!; PIDS+=("$ENGINE")

( for _ in $(seq 1 300); do
    if curl -sf -m 3 http://127.0.0.1:3001/api/health/live >/dev/null 2>&1; then
      printf '\n  Nidra is ready. Open:\n\n    %s\n\n  The engine API is on :3001 (see the README). Your data stays in the volume at /data.\n\n' "$(link)"
      exit 0
    fi
    sleep 1
  done; say "the engine has not answered after five minutes" ) &
HELPERS+=($!)

status=0
wait -n "${PIDS[@]}" || status=$?
say "a process stopped (status $status); stopping the others"
trap - TERM INT
stop_all
exit $(( status == 0 ? 1 : status ))
