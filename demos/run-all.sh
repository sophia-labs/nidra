#!/usr/bin/env bash
# Run every demo in order against the live stack and write demos/out/SUMMARY.txt.
#
#   demos/run-all.sh                     # ask the engine every question afresh
#   DEMOS_REUSE_ANSWERS=1 demos/run-all.sh   # reuse kept answers (demos/state/answers), ask only the missing ones
#
# Needs: the Nidra engine on :3001, the nidra-ingestion container, the scratch
# gardend on :7690 (token file: GARDEND_TOKEN_FILE, see lib/env.sh), Postgres on
# :5432 for Pagila, and model credit on the key in the fork's .env (a full run
# asks about 15 questions; at Sonnet prices that was roughly $0.05-0.10 each tonight).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/lib/env.sh"
bash "$HERE/lib/credit.sh" || true
bash "$HERE/lib/side-engine.sh" start || { echo "side engine did not start" >&2; exit 1; }
trap 'bash "$HERE/lib/side-engine.sh" stop' EXIT
for script in "$HERE"/[0-9][0-9]*-*.py; do
  name="$(basename "$script" .py)"
  echo "=== $name"
  "$FORK_DIR/.venv/bin/python" "$script" > /dev/null 2> "$DEMOS_OUT/$name.stderr" || echo "    (exited $?; see out/$name.stderr)"
  [ -s "$DEMOS_OUT/$name.stderr" ] || rm -f "$DEMOS_OUT/$name.stderr"
  grep -h '^STATUS:' "$DEMOS_OUT/$name.txt" 2>/dev/null | sed 's/^/    /'
done
{
  echo "# demo statuses, $(date '+%Y-%m-%d %H:%M %Z')"
  for f in "$DEMOS_OUT"/[0-9][0-9]*-*.txt; do
    printf '%-32s %s\n' "$(basename "$f" .txt)" "$(grep -h '^STATUS:' "$f" | sed 's/^STATUS: //')"
  done
} > "$DEMOS_OUT/SUMMARY.txt"
cat "$DEMOS_OUT/SUMMARY.txt"
bash "$HERE/lib/credit.sh" || true
