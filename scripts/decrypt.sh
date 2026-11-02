#!/usr/bin/env bash
# Decifra .env.enc nel .env locale (600).
#
#   ./scripts/decrypt.sh
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck source=lib/age.sh
. "$ROOT/scripts/lib/age.sh"

require_sops
require_age_key "$ROOT" >/dev/null

if [[ ! -f .env.enc ]]; then
  echo "manca .env.enc: lancia prima ./scripts/encrypt.sh" >&2
  exit 1
fi

sops --config .sops.yaml --decrypt .env.enc > .env
chmod 600 .env
echo ".env decifrato"
