# Sourced by every demo: run from the fork root with the fork's .env, the
# scratch gardend and the Nidra engine. Prints nothing; never echoes a secret.
export DEMOS_DIR; DEMOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export FORK_DIR; FORK_DIR="$(cd "$DEMOS_DIR/.." && pwd)"
cd "$FORK_DIR" || exit 1
set -a
# shellcheck disable=SC1091
. ./.env
set +a
export NIDRA_URL="${NIDRA_URL:-http://127.0.0.1:3001}"
export NIDRA_USER="${NIDRA_USER:-vera}"
export GARDEND_URL="${DEMO_GARDEND_URL:-http://127.0.0.1:7690}"
export GARDEND_GRAPH_ID="${DEMO_GARDEND_GRAPH_ID:-ao-catalog}"
export GARDEND_TOKEN_FILE="${GARDEND_TOKEN_FILE:-/private/tmp/claude-501/-Users-vera-dev-sophia/81a8c7ee-31df-468d-8c4e-c98cf7ba15c2/scratchpad/gd/token-all.json}"
GARDEND_TOKEN="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["token"])' "$GARDEND_TOKEN_FILE")"
export GARDEND_TOKEN
export CATALOG_READ_BACKEND=rdf
export DEMOS_OUT="$DEMOS_DIR/out"
mkdir -p "$DEMOS_OUT"
