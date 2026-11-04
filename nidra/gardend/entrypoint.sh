#!/bin/sh
# Nidra's gardend: serve the profile, make sure the catalog graph exists, turn
# on `find` for it and keep its index fresh. With arguments, run gardend itself
# (e.g. `token create`).
set -eu
if [ "$#" -gt 0 ]; then exec /usr/local/bin/gardend "$@"; fi
: "${GARDEN_LOOPBACK_TOKEN:?set GARDEN_LOOPBACK_TOKEN}"
GRAPH="${NIDRA_GRAPH_ID:-nidra}"; PORT="${GARDEN_LOOPBACK_PORT:-8086}"; EVERY="${NIDRA_INDEX_EVERY_SECONDS:-600}"
/usr/local/bin/gardend serve --profile "${GARDEN_PROFILE_DIR:-/data/profile}" --port "$PORT" &
SERVE=$!
trap 'kill -TERM $SERVE 2>/dev/null; wait $SERVE' TERM INT
api() { curl -sS -m 20 -H "Authorization: Bearer $GARDEN_LOOPBACK_TOKEN" -H 'content-type: application/json' "$@"; }
for i in $(seq 1 60); do curl -sf -m 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break; sleep 1; done
if ! api -X POST "http://127.0.0.1:$PORT/api/sparql/query" -d "{\"graphId\":\"$GRAPH\",\"sparql\":\"ASK { }\"}" 2>/dev/null | grep -q '"resultType"'; then
  api -X POST "http://127.0.0.1:$PORT/api/graphs" -d "{\"graphId\":\"$GRAPH\",\"title\":\"Nidra catalog\"}" >/dev/null && echo "nidra: created graph $GRAPH"
fi
# find: enable and build once (the model is baked in; no network), then refresh.
( while kill -0 $SERVE 2>/dev/null; do
    /usr/local/bin/gardend index --graph "$GRAPH" --profile "${GARDEN_PROFILE_DIR:-/data/profile}" 2>&1 | grep -E 'entity index|failed' || true
    sleep "$EVERY"
  done ) &
wait $SERVE
