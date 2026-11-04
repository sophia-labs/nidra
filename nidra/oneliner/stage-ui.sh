#!/usr/bin/env bash
# Put the Sophia UI (built with the /nidra screens) where Dockerfile.nidra takes it: nidra/oneliner/ui.
#
#   nidra/oneliner/stage-ui.sh [<built ui folder>]
#
# The folder is a Shrubbery organism build with SHRUBBERY_EXTENSIONS=packages/nidra-app (gardend's
# `scripts/buildbox app --only ui` makes one), or the ui/ of an installed Sophia.app that has Nidra
# (the default). It is a build output, so it is not kept in git.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SRC=${1:-$HOME/Applications/Sophia.app/Contents/Resources/ui}
[ -f "$SRC/index.html" ] || { echo "stage-ui: no index.html in $SRC" >&2; exit 1; }
grep -rqs 'nidra-app\|nidra-settings' "$SRC/assets" || { echo "stage-ui: $SRC has no /nidra screens" >&2; exit 1; }
# The container carries no Sophia Compute: a UI whose Ask is a Compute conversation (round 2, nidra-chat-v1) would have
# nothing to talk to there. Until the image carries Compute, it takes the round-1 UI, whose Ask goes to the engine.
grep -rqs 'nidra-chat-v1' "$SRC/assets" && { echo "stage-ui: $SRC asks through Sophia Compute (nidra-chat-v1); the container has none. Stage the round-1 UI (e.g. ~/.cache/gardend-buildbox/Sophia.pre-nidra-being-20261103.app/Contents/Resources/ui)" >&2; exit 1; }
rm -rf "$HERE/ui" && cp -R "$SRC" "$HERE/ui"
echo "staged $(du -sh "$HERE/ui" | cut -f1) from $SRC"
