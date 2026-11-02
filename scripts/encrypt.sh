#!/usr/bin/env bash
# Cifra .env in .env.enc con la identità age di flotta.
#
#   ./scripts/encrypt.sh
#
# Il file in chiaro resta locale e gitignored; solo .env.enc va committato.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck source=lib/age.sh
. "$ROOT/scripts/lib/age.sh"

require_sops
require_age_key "$ROOT" >/dev/null

if [[ ! -f .env ]]; then
  echo "manca .env: copia .env.example e compila i valori" >&2
  exit 1
fi

temporary="$(mktemp .env.enc.tmp.XXXXXX)"
cleanup() { rm -f "$temporary"; }
trap cleanup EXIT INT TERM

sops --config .sops.yaml --encrypt --input-type binary --output-type json .env > "$temporary"
mv -f "$temporary" .env.enc
chmod 600 .env.enc
echo ".env.enc aggiornato"
