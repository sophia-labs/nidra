#!/usr/bin/env bash
# The closure test: seed NVIDIA's graph fixture through the engine with the
# catalog on gardend and the Postgres *store* unreachable, then replay the
# DAL golden in strict mode. Postgres remains only as the Pagila *source*
# (the warehouse being catalogued); nothing of the engine's own state is in it.
#
#   GARDEND_URL=http://127.0.0.1:7691 GARDEND_TOKEN=... nidra/closure.sh
#
# NIDRA_WRITES=commit (passed through) seeds through gardend's Emporium ledger
# instead of SPARQL updates; that needs a gardend with the commit seam.
set -euo pipefail
cd "$(dirname "$0")/.."
_URL="${GARDEND_URL:-}"; _TOKEN="${GARDEND_TOKEN:-}"; _GRAPH="${GARDEND_GRAPH_ID:-}"
set -a; . ./.env; set +a
# the caller's gardend wins over .env (which names the compose gardend)
export GARDEND_URL="${_URL:-$GARDEND_URL}" GARDEND_TOKEN="${_TOKEN:-$GARDEND_TOKEN}"
: "${GARDEND_URL:?}" "${GARDEND_TOKEN:?}"
SQ="$PWD/dev_tools/fixtures/sql/chinook.sqlite"
export CONNECTION_STRINGS="postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@localhost:$POSTGRES_PORT/pagila,sqlite:///$SQ"
export GARDEND_GRAPH_ID="${_GRAPH:-closure-$(date +%s)}"
export CATALOG_READ_BACKEND=rdf AO_RDF_STRICT=1
# the engine's store must be unreachable: only the source URL above names Postgres
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT_STORE_UNREACHABLE=1
echo "== graph $GARDEND_GRAPH_ID on $GARDEND_URL; Postgres store unreachable, Pagila source on :$POSTGRES_PORT"
uv run --no-sync python -c "from auto_ontology.rdf.client import client; client().ensure_graph('closure'); print('graph ready')"
export POSTGRES_PORT=1
echo "== seed through the engine (ingest + hand-seeded semantic layer), all writes to gardend"
uv run --no-sync python -m dev_tools.fixtures.seed_graph_fixture --reset 2>&1 | grep -vE 'HTTP Request|WARNING psycopg' | tail -5
echo "== golden replay, strict"
uv run --no-sync pytest auto_ontology/dal/tests/test_golden.py -q 2>&1 | tail -2
