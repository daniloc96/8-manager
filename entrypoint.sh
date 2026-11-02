#!/bin/sh
# Entrypoint del container: permessi stretti, migrazioni, poi il bot.
set -eu

umask 077
: "${DATA_DIR:=/data}"
mkdir -p "$DATA_DIR" "$DATA_DIR/backups"

# Con un comando esplicito (es. `docker compose run bot alembic upgrade head`)
# eseguiamo solo quello: le migrazioni restano un passo controllato dal deploy.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

alembic upgrade head
exec python -m app.main
