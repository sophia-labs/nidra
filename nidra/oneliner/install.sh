#!/bin/sh
# Nidra: ask your databases questions in plain words.
#
#   curl -fsSL https://nidra.computer/install.sh | sh
#
# What this does: checks that Docker runs, asks for your OpenRouter key (unless OPENROUTER_API_KEY is set),
# starts one container, ghcr.io/sophia-labs/nidra, with your data in a Docker volume called `nidra`, waits
# until it answers and prints the link to open. Running it again updates Nidra and keeps your data.
# Everything is reachable from this computer only (127.0.0.1).
#
# Settings, all optional: NIDRA_MODEL (an OpenRouter model id), NIDRA_PORT (8086), NIDRA_API_PORT (3001),
# NIDRA_NO_OPEN=1 (print the link, do not open a browser).
set -eu

IMAGE=${NIDRA_IMAGE:-ghcr.io/sophia-labs/nidra:latest}
NAME=${NIDRA_NAME:-nidra}
VOLUME=${NIDRA_VOLUME:-nidra}
PORT=${NIDRA_PORT:-8086}
API_PORT=${NIDRA_API_PORT:-3001}

fail() { printf '%s\n' "$*" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || fail "Nidra runs in Docker. Install Docker Desktop (https://docs.docker.com/get-docker/), start it, then run this again."
docker info >/dev/null 2>&1 || fail "Docker is installed but not running. Start Docker Desktop, then run this again."

if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  { : </dev/tty; } 2>/dev/null || fail "Set OPENROUTER_API_KEY (your key from https://openrouter.ai/keys) and run this again."
  printf 'Your OpenRouter key (sk-or-..., from https://openrouter.ai/keys; it is not shown as you type): '
  stty -echo </dev/tty 2>/dev/null || true
  read -r OPENROUTER_API_KEY </dev/tty || true
  stty echo </dev/tty 2>/dev/null || true
  echo
fi
[ -n "${OPENROUTER_API_KEY:-}" ] || fail "No key given."
export OPENROUTER_API_KEY   # docker reads it from here (-e OPENROUTER_API_KEY): it never appears in a command line

echo "Getting Nidra (the first time this downloads about 0.8 GB)..."
docker pull "$IMAGE" >/dev/null 2>&1 || docker image inspect "$IMAGE" >/dev/null 2>&1 || fail "Could not download $IMAGE."
if [ -n "$(docker ps -aq --filter "name=^${NAME}\$")" ]; then
  echo "Replacing the Nidra container you had (your data stays in the volume $VOLUME)."
  docker rm -f "$NAME" >/dev/null
fi
set -- -e OPENROUTER_API_KEY -e "NIDRA_URL=http://localhost:$PORT"
[ -z "${NIDRA_MODEL:-}" ] || set -- "$@" -e "NIDRA_MODEL=$NIDRA_MODEL"
docker run -d --name "$NAME" --restart unless-stopped --stop-timeout 30 \
  -p "127.0.0.1:$PORT:8086" -p "127.0.0.1:$API_PORT:3001" -v "$VOLUME:/data" "$@" "$IMAGE" >/dev/null

printf 'Starting'
i=0
until docker exec "$NAME" curl -sf -m 3 http://127.0.0.1:3001/api/health/live >/dev/null 2>&1; do
  [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = true ] || { echo; docker logs --tail 30 "$NAME" >&2; fail "Nidra stopped. Its last words are above."; }
  i=$((i + 1)); [ "$i" -lt 300 ] || { echo; fail "Nidra did not answer within five minutes; see: docker logs $NAME"; }
  printf '.'; sleep 1
done
URL=$(docker exec "$NAME" nidra url)
printf '\n\nNidra is running. Open this link (it holds your key; keep it to yourself):\n\n  %s\n\n' "$URL"
echo "It reads the sample database first (Chinook, a music store); give it a minute or two."
echo "Stop: docker stop $NAME   Start: docker start $NAME   The link again: docker exec $NAME nidra url"
if [ -n "${NIDRA_NO_OPEN:-}" ]; then :
elif command -v open >/dev/null 2>&1; then open "$URL" 2>/dev/null || true
elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true; fi
