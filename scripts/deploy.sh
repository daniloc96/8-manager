#!/usr/bin/env bash
# Deploy del bot sull'host Hetzner personale.
#
#   ./scripts/deploy.sh            # dry-run, nessuna scrittura remota
#   ./scripts/deploy.sh --apply    # build, migrazioni e avvio sul server
#
# Il default è dry-run: --apply richiede una decisione esplicita.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MODE="dry-run"
case "${1:-}" in
  "" | --dry-run) MODE="dry-run" ;;
  --apply) MODE="apply" ;;
  *)
    echo "Uso: $0 [--dry-run|--apply]" >&2
    exit 2
    ;;
esac

# shellcheck source=lib/remote.sh
. "$ROOT/scripts/lib/remote.sh"
prepare_remote "$ROOT"

log() { printf '[deploy] %s\n' "$*"; }
fail() {
  printf '[deploy] ERROR: %s\n' "$*" >&2
  exit 1
}

[[ -f "$ROOT/.env" ]] || fail ".env mancante: copia .env.example e compila i valori"

# Se .env è più recente della copia cifrata, la riallineiamo: il deploy non
# parte mai con un segreto vecchio.
if [[ ! -f "$ROOT/.env.enc" || "$ROOT/.env" -nt "$ROOT/.env.enc" ]]; then
  log "aggiorno .env.enc"
  "$ROOT/scripts/encrypt.sh" >/dev/null
fi

sops --config "$ROOT/.sops.yaml" --decrypt "$ROOT/.env.enc" > "$TMP_DIR/env"
chmod 600 "$TMP_DIR/env"

for var in BOT_TOKEN OWNER_TELEGRAM_ID; do
  grep -qE "^${var}=.+" "$TMP_DIR/env" || fail ".env.enc non contiene $var"
done

log "host: $REMOTE · path: $DEPLOY_PATH · modalità: $MODE"

remote_ssh true || fail "SSH non raggiungibile"

free_kb="$(remote_ssh "df -Pk \"\$HOME\" | awk 'NR==2 {print \$4}'")"
[[ "$free_kb" =~ ^[0-9]+$ ]] || fail "spazio disco non leggibile: $free_kb"
free_mb=$((free_kb / 1024))
((free_mb >= MIN_FREE_DISK_MB)) ||
  fail "spazio libero ${free_mb} MB < ${MIN_FREE_DISK_MB} MB: libera il disco (docker builder prune -f) e riprova"
log "spazio libero: ${free_mb} MB"

remote_ssh "command -v docker >/dev/null && docker compose version >/dev/null" ||
  fail "docker compose non disponibile sul server"

if [[ "$MODE" == "dry-run" ]]; then
  log "dry-run: nessuna scrittura eseguita"
  exit 0
fi

log "sync sorgenti"
rsync -az --delete \
  --exclude '.git' --exclude '.env' --exclude '.env.enc' \
  --exclude '.sops' --exclude 'secrets' --exclude 'data' \
  --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude '.ruff_cache' --exclude '.playwright-mcp' \
  -e "ssh ${SSH_OPTS[*]}" \
  "$ROOT/" "$REMOTE:$DEPLOY_PATH/"

log "scrivo .env sul server (600)"
remote_ssh "mkdir -p '$DEPLOY_PATH/data/backups' && umask 077 && cat > '$DEPLOY_PATH/.env' && chmod 600 '$DEPLOY_PATH/.env'" <"$TMP_DIR/env"

log "build immagine"
remote_ssh "cd '$DEPLOY_PATH' && COMPOSE_DISABLE_ENV_FILE=1 docker compose -f docker-compose.prod.yaml build bot"

log "migrazioni"
remote_ssh "cd '$DEPLOY_PATH' && COMPOSE_DISABLE_ENV_FILE=1 docker compose -f docker-compose.prod.yaml run --rm --no-deps bot alembic upgrade head"

log "avvio"
remote_ssh "cd '$DEPLOY_PATH' && COMPOSE_DISABLE_ENV_FILE=1 docker compose -f docker-compose.prod.yaml up -d bot"

log "attendo healthcheck"
healthy="no"
for _ in $(seq 1 30); do
  status="$(remote_ssh "docker inspect --format '{{.State.Health.Status}}' eight-manager-bot 2>/dev/null || echo missing")"
  case "$status" in
    healthy)
      healthy="yes"
      break
      ;;
    unhealthy)
      remote_ssh "cd '$DEPLOY_PATH' && COMPOSE_DISABLE_ENV_FILE=1 docker compose -f docker-compose.prod.yaml logs --tail=60 bot" >&2 || true
      fail "bot unhealthy"
      ;;
    *) sleep 2 ;;
  esac
done
[[ "$healthy" == "yes" ]] || fail "healthcheck non superato in 60s"

remote_ssh "cd '$DEPLOY_PATH' && COMPOSE_DISABLE_ENV_FILE=1 docker compose -f docker-compose.prod.yaml ps"
remote_ssh "cd '$DEPLOY_PATH' && COMPOSE_DISABLE_ENV_FILE=1 docker compose -f docker-compose.prod.yaml logs --tail=20 bot"

log "deploy completato"
