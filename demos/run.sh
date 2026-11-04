#!/usr/bin/env bash
# Run one demo with the fork's environment: demos/run.sh 03-ask-count.py
set -uo pipefail
. "$(dirname "$0")/lib/env.sh"
script="$1"; shift || true
case "$script" in
  *.py) exec "$FORK_DIR/.venv/bin/python" "$DEMOS_DIR/$(basename "$script")" "$@" ;;
  *.sh) exec bash "$DEMOS_DIR/$(basename "$script")" "$@" ;;
esac
