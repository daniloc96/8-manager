#!/usr/bin/env bash
# Prepara una connessione SSH autenticata con la chiave cifrata del progetto.
#
# Uso:
#   source scripts/lib/remote.sh
#   prepare_remote "$ROOT"
#   remote_ssh "hostname"
#
# Richiede che il chiamante abbia già impostato `set -euo pipefail`.

prepare_remote() {
  local root="$1"

  cd "$root"
  # shellcheck source=/dev/null
  set -a
  . "$root/deploy.env"
  set +a

  : "${DEPLOY_HOST:?}" "${DEPLOY_USER:?}" "${DEPLOY_PORT:=22}" \
    "${SSH_KEY_FILE:?}" "${KNOWN_HOSTS_FILE:?}" "${DEPLOY_PATH:?}" \
    "${APP_NAME:?}"

  # shellcheck source=age.sh
  . "$root/scripts/lib/age.sh"
  require_sops
  require_age_key "$root" >/dev/null

  [[ -f "$root/$SSH_KEY_FILE" ]] || {
    echo "chiave SSH cifrata mancante: $SSH_KEY_FILE" >&2
    return 1
  }
  [[ -f "$root/$KNOWN_HOSTS_FILE" ]] || {
    echo "known_hosts mancante: $KNOWN_HOSTS_FILE" >&2
    return 1
  }

  TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/8manager-remote.XXXXXX")"
  chmod 700 "$TMP_DIR"
  trap 'rm -rf "$TMP_DIR"' EXIT INT TERM

  sops --config "$root/.sops.yaml" --decrypt "$root/$SSH_KEY_FILE" > "$TMP_DIR/deployer"
  chmod 600 "$TMP_DIR/deployer"

  SSH_OPTS=(
    -i "$TMP_DIR/deployer"
    -p "$DEPLOY_PORT"
    -o BatchMode=yes
    -o ConnectTimeout=15
    -o StrictHostKeyChecking=yes
    -o UserKnownHostsFile="$root/$KNOWN_HOSTS_FILE"
    -o IdentitiesOnly=yes
  )
  REMOTE="$DEPLOY_USER@$DEPLOY_HOST"
}

remote_ssh() {
  ssh "${SSH_OPTS[@]}" "$REMOTE" "$@"
}
