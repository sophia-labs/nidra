#!/usr/bin/env bash
# Build Nidra's gardend service folder: what goes in <app>/Contents/Resources/services/nidra (or any
# GARDEN_SERVICES_DIR/nidra). The engine itself is a Docker image (nidra-engine:latest, from
# `docker compose -f compose.nidra.yaml build auto-ontology` in this checkout, or `docker load`); this folder
# carries the supervisor, the compose files and the sample source.
#
#   nidra/service/build.sh <out-dir>      # e.g. .../services/nidra
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
OUT=${1:?out dir, e.g. <services>/nidra}
[[ $(basename "$OUT") == nidra ]] || { echo "build.sh: the folder must be named nidra (it is the service id)" >&2; exit 1; }
rm -rf "$OUT" && mkdir -p "$OUT/engine/nidra/data"
cp "$HERE/service.json" "$HERE/main.mjs" "$HERE/asks.mjs" "$HERE/compose.sophia.yaml" "$OUT/"
cp "$ROOT/compose.nidra.yaml" "$OUT/engine/"
cp "$ROOT/nidra/data/chinook.sqlite" "$OUT/engine/nidra/data/"
cp "$ROOT/LICENSE" "$ROOT/NIDRA-NOTICE.md" "$OUT/" 2>/dev/null || true
image=$(docker image inspect --format '{{.Id}}' nidra-engine:latest 2>/dev/null || echo "not loaded here")
cat >"$OUT/BUILD.json" <<JSON
{
  "service": "nidra",
  "built": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "source": "$(git -C "$ROOT" rev-parse --short HEAD)$(git -C "$ROOT" diff --quiet -- nidra/service compose.nidra.yaml || echo '+dirty')",
  "engineImage": "nidra-engine:latest",
  "engineImageId": "$image"
}
JSON
echo "$OUT"
