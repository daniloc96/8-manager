# 8-manager

Bot Telegram per gestire abbonamenti, rinnovi e scadenze di una pay TV.
Un solo owner, più rivenditori, linee Live e Live+VOD, rinnovi a mesi di
calendario.

Stato: **fasi A, P, S, B, C e D completate e in produzione** (2026-11-02).
Il bot gira su `bitch-0` come `@l7_manager_bot`; l'import ha caricato 176 righe
di pannello (84 clienti). Cosa sa fare oggi:

- menu con scadenze (scadute, oggi, prossimi giorni), ricerca libera e scheda
  linea con credenziali dei pannelli;
- rinnovo con un tap, prezzo modificabile, storico e undo del rinnovo entro
  15 minuti;
- incassi del mese raggruppati per rivenditore, registro rivenditori;
- wizard `/nuovo` per creare cliente e linea, con il blocco credenziali da
  copiare nei pannelli;
- rivenditori autorizzati dall'owner: vedono solo le proprie linee e inviano
  richieste di rinnovo che l'owner approva o rifiuta;
- reminder giornaliero alle 09:00, backup notturno alle 03:15, entrambi con
  deduplica su `notifications`;
- sync dei pannelli con review, eccezioni accettate e chiusura delle linee
  sparite (`prune-missing`).

## Stack

| Componente | Scelta |
| --- | --- |
| Linguaggio | Python 3.13 |
| Bot | `python-telegram-bot` 22.8, polling |
| Database | SQLite in `/data`, WAL, backup con API sqlite3 |
| ORM | SQLAlchemy 2 async + Alembic |
| Dependencies | `uv` con lock |
| Deploy | Docker Compose su VPS Hetzner, SOPS/age |

## Struttura

```
app/
  config.py        impostazioni da .env
  db.py            engine async e sessioni
  models/          clienti, linee, rinnovi, pannelli, audit
  services/        mesi, testi, creazione e rinnovo
  panels/          adapter 8Plus/LUCKY/VOD, sync, import, report
  bot/             handler Telegram
  jobs/            heartbeat (poi reminder notturni)
  cli.py           migrate, check, backup, seed-demo, sync
  healthcheck.py   heartbeat + database
migrations/        Alembic
scripts/           deploy, cifratura, backup, verify
secrets/ssh/       chiave deployer cifrata + known_hosts
docs/              architettura, modello dati, flussi, runbook, pannelli
```

## Avvio locale

```bash
cp .env.example .env
# compila BOT_TOKEN e OWNER_TELEGRAM_ID (per l'ID: @userinfobot)
chmod 600 .env

uv sync
uv run pytest
uv run ruff check .

# database locale e dati finti
export DATABASE_URL=sqlite+aiosqlite:///./data/dev.sqlite3
mkdir -p data
export DATA_DIR=./data
uv run python -m app.cli migrate
uv run python -m app.cli seed-demo
uv run python -m app.cli check
```

Con Docker:

```bash
docker compose up -d --build
docker compose logs -f bot
```

Il container non pubblica porte: il bot usa il polling in uscita.

## Comandi CLI

```bash
python -m app.cli migrate     # applica le migrazioni
python -m app.cli check       # configurazione e conteggi
python -m app.cli backup      # copia consistente in /data/backups
python -m app.cli seed-demo   # due clienti finti, solo per prove
python -m app.cli sync        # sync dai pannelli configurati
python -m app.cli sync --import          # importa anche le linee nuove
python -m app.cli sync --dry-run         # legge e calcola, non scrive
python -m app.cli sync --snapshot data   # offline, dagli snapshot
```

## Deploy

Guida completa in [DEPLOYMENT.md](DEPLOYMENT.md). In breve:

```bash
./scripts/deploy.sh            # dry-run, nessuna scrittura
./scripts/deploy.sh --apply    # build, migrazioni, avvio
./scripts/verify.sh            # stato, health, disco, log
```

## Pannelli

La discovery è documentata in [docs/panels-api.md](docs/panels-api.md):
endpoint, schemi, pacchetti di rinnovo, accoppiamento Live↔VOD e verdetto.
Lo snapshot completo è in `data/panel-*.json` (gitignored, 600): 176 linee
tra 8Plus, LUCKY LIVE e VOD.

Il sync è in `app/panels/`:

- `eightplus.py` — JWT + refresh, lista codici;
- `lucky.py` — cookie + CSRF, usato sia per LUCKY LIVE sia per VOD;
- `sync.py` — upsert, accoppiamento per username, import, riconciliazione;
- `packages.py` — pacchetti risolti per nome, durate 8Plus in giorni.

Prima esecuzione consigliata:

```bash
python -m app.cli sync --import --dry-run   # vede cosa farebbe
python -m app.cli sync --import             # scrive
python -m app.cli sync                      # sync normale
```

L'import crea cliente e linea con prezzo `0` e nota "prezzo da impostare":
i prezzi commerciali si completano dal bot nella fase B. L'accoppiamento
Live↔VOD avviene solo per `username` normalizzato, con verifica della nota;
le eccezioni restano in "da rivedere" e non vengono indovinate.

LUCKY LIVE e VOD sono su HTTP: il sync va eseguito da rete fidata o VPN.
Se il server non li raggiunge, usare `--snapshot` in locale o spostare il
sync su una macchina con accesso.

## Sicurezza

- `.env` e `.sops/keys.txt` sono gitignored; in git entrano solo `.env.enc`,
  la chiave SSH cifrata e `known_hosts`.
- SSH sempre con `StrictHostKeyChecking=yes` e host key pinnate.
- Il container gira come UID 1000 e scrive con umask 077: database e backup
  sono `600`.
- Nessun `docker compose down`, `prune` o `--remove-orphans` in deploy: la
  macchina ospita altri stack.

## Roadmap

| Fase | Contenuto |
| --- | --- |
| A ✅ | fondamenta, modelli, mesi, Docker, SOPS, deploy |
| P ✅ | discovery read-only dei pannelli, verdetto curl/API |
| B | clienti e linee, creazione bot-first, bottone "Fatto", reminder, ricerca |
| S ✅ | sync pannelli, import iniziale, classificazione, riconciliazione |
| C | accesso rivenditori, richieste, approvazioni, gruppo |
| D | incassi, export, insoluti, rifiniture |
| E | backup/restore provato, runbook, censimento VPS |
| F | auto-rinnovo via curl, solo se la fase P lo rende solido |
