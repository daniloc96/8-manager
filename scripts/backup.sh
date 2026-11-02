#!/usr/bin/env bash
# Backup manuale del database dentro il container.
#
#   ./scripts/backup.sh
#
# Il backup notturno automatico è un job del bot (fase D).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck source=lib/remote.sh
. "$ROOT/scripts/lib/remote.sh"
prepare_remote "$ROOT"

remote_ssh "docker exec eight-manager-bot python -m app.cli backup"
