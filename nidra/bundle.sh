#!/usr/bin/env bash
# Build the Nidra local download: the source bundle and the gardend image.
#   nidra/bundle.sh <out-dir>
# Produces nidra-local-<date>.tar.gz (this checkout at HEAD: engine source,
# compose.nidra.yaml, nidra/, no secrets) and nidra-gardend-<date>.tar (the
# gardend image with ONNX Runtime, for `docker load`), plus SHA-256 sums.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:?out dir}"; mkdir -p "$OUT"; D=$(date +%Y%m%d)
git archive --format=tar.gz --prefix="nidra-local-$D/" -o "$OUT/nidra-local-$D.tar.gz" HEAD
docker build -q -t "nidra-gardend:$D" nidra/gardend >/dev/null && docker tag "nidra-gardend:$D" nidra-gardend:latest
docker save "nidra-gardend:$D" nidra-gardend:latest -o "$OUT/nidra-gardend-$D.tar"
(cd "$OUT" && shasum -a 256 "nidra-local-$D.tar.gz" "nidra-gardend-$D.tar" > "SHA256SUMS-$D.txt")
ls -la "$OUT"
