#!/usr/bin/env bash
# Controlli read-only sullo stack in produzione.
#
#   ./scripts/verify.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck source=lib/remote.sh
. "$ROOT/scripts/lib/remote.sh"
prepare_remote "$ROOT"

echo "== compose =="
remote_ssh "cd '$DEPLOY_PATH' && COMPOSE_DISABLE_ENV_FILE=1 docker compose -f docker-compose.prod.yaml ps"

echo "== health =="
remote_ssh "docker inspect --format '{{.State.Health.Status}}' eight-manager-bot"

echo "== disco =="
remote_ssh "df -h '$DEPLOY_PATH' | tail -1"

echo "== log (ultime 30 righe) =="
remote_ssh "cd '$DEPLOY_PATH' && COMPOSE_DISABLE_ENV_FILE=1 docker compose -f docker-compose.prod.yaml logs --tail=30 bot"
