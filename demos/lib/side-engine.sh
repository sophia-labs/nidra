#!/usr/bin/env bash
# A second Nidra engine on :3011, the same code as the live one on :3001 and
# attached to the same gardend graph, with one difference: CONNECTION_STRINGS
# names Pagila (Postgres) as well as Chinook (SQLite). The live engine's
# CONNECTION_STRINGS names Chinook only, so it cannot execute Pagila SQL (see
# demos 02 and 03). Adding Pagila through POST /api/connections would shadow
# CONNECTION_STRINGS (UI connections win in auto_ontology/connectors/registry.py)
# and SQLite has no UI connection type, so the live engine would lose Chinook.
#
#   demos/lib/side-engine.sh start [graph-id]   # default graph: ao-catalog
#   demos/lib/side-engine.sh stop
set -euo pipefail
. "$(dirname "$0")/env.sh"
STATE="$DEMOS_DIR/state"; mkdir -p "$STATE"
PORT="${SIDE_ENGINE_PORT:-3011}"
PIDF="$STATE/side-engine-$PORT.pid"
case "${1:-start}" in
  start)
    if curl -s -m 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then echo "side engine already up on :$PORT"; exit 0; fi
    GRAPH="${2:-ao-catalog}"
    CS="$(python3 "$DEMOS_DIR/lib/conn_strings.py")"
    # POSTGRES_HOST/PORT point nowhere, as in the live container: any DAL read
    # that still reaches for Postgres fails here too instead of quietly finding
    # the host's Postgres (the Pagila source is named in CONNECTION_STRINGS).
    CONNECTION_STRINGS="$CS" GARDEND_GRAPH_ID="$GRAPH" AO_RDF_STRICT=1 \
      POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=1 \
      INGESTION_SERVICE_URL=http://127.0.0.1:9 \
      nohup "$FORK_DIR/.venv/bin/uvicorn" auto_ontology.server.__main__:create_app --factory \
        --host 127.0.0.1 --port "$PORT" > "$STATE/side-engine-$PORT.log" 2>&1 &
    echo $! > "$PIDF"
    for _ in $(seq 1 90); do
      if curl -s -m 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then echo "side engine up on :$PORT (graph $GRAPH, pid $(cat "$PIDF"))"; exit 0; fi
      sleep 1
    done
    echo "side engine did not come up; see $STATE/side-engine-$PORT.log" >&2; exit 1 ;;
  stop)
    if [ -f "$PIDF" ]; then
      PID="$(cat "$PIDF")"
      # Only the PID this script started, and only if it is still our uvicorn.
      if ps -o command= -p "$PID" 2>/dev/null | grep -q "auto_ontology.server.__main__:create_app"; then kill "$PID"; echo "stopped side engine pid $PID"; fi
      rm -f "$PIDF"
    fi ;;
esac
