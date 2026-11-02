# Runbook

## Accesso

```bash
cd /Users/danilocacace/Git/Personal/8-manager
./scripts/verify.sh          # stato, health, disco, log
./scripts/backup.sh          # backup manuale
./scripts/deploy.sh          # dry-run
./scripts/deploy.sh --apply  # deploy
```

SSH interattivo (la chiave viene decifrata in una directory temporanea):

```bash
source scripts/lib/remote.sh
prepare_remote "$PWD"
remote_ssh
```

## Comandi sul server

```bash
cd /home/deployer/8-manager

# COMPOSE_DISABLE_ENV_FILE=1 evita che Compose interpreti il '$' nelle password.
COMPOSE_DISABLE_ENV_FILE=1 docker compose -f docker-compose.prod.yaml ps
COMPOSE_DISABLE_ENV_FILE=1 docker compose -f docker-compose.prod.yaml logs -f --tail=100 bot
COMPOSE_DISABLE_ENV_FILE=1 docker compose -f docker-compose.prod.yaml restart bot
docker exec -it eight-manager-bot python -m app.cli check
docker exec -it eight-manager-bot python -m app.cli backup
docker exec -it eight-manager-bot python -m app.cli sync --dry-run
docker exec -it eight-manager-bot python -m app.cli sync
docker exec -it eight-manager-bot python -m app.cli sync --import
docker exec -it eight-manager-bot python -m app.cli prune-missing
docker exec -it eight-manager-bot python -m app.cli exceptions
```

Mai `docker compose down`, `prune` o `--remove-orphans`: la macchina ospita
altri stack.

## Sync pannelli

Ordine consigliato:

```bash
# 1. legge i pannelli e mostra cosa cambierebbe, senza scrivere
python -m app.cli sync --import --dry-run

# 2. primo import (crea clienti e linee con prezzo 0)
python -m app.cli sync --import

# 3. sync normale, da qui in avanti
python -m app.cli sync
```

Se i pannelli HTTP non sono raggiungibili (es. dalla VPS senza VPN), in
locale si può importare dagli snapshot scaricati dalla discovery:

```bash
python -m app.cli sync --snapshot data --dry-run
python -m app.cli sync --snapshot data --import
```

Gli snapshot non vengono copiati sul server dal deploy: restano in `data/`
sulla macchina locale (gitignored).

Dal bot, solo owner: `/sync` e `/sync importa`.

## Bot: comandi e flussi

Owner:

```bash
/start        menu con scadenze, ricerca, nuovo cliente, incassi, rivenditori
/scadenze     scadute, oggi, prossimi giorni (le eccezioni attive sono nascoste)
/cerca Mario  ricerca fra nome, alias, etichetta, username e ID
/nuovo        wizard: cliente + prima linea, con credenziali da copiare
/incassi      rinnovi del mese per rivenditore
/richiedi     solo rivenditori: richiesta di rinnovo
/stato /sync /id /help
```

Dalla scheda linea: **Rinnova** (1/2/3/5/12 mesi o importo libero), **Prezzo**,
**Storico**. Il rinnovo mostra **Annulla** per 15 minuti: annulla solo lato bot,
il pannello va corretto a mano.

Non c'è un pulsante per disattivare: una linea esce dal bot solo quando le sue
righe spariscono dai pannelli. Dopo la sync `prune-missing` chiude le linee con
tutte le righe sparite (dry-run di default, `--force` sopra il 30% di righe
mancanti) e cancella le righe orfane. Per un errore c'è `line-restore --line N`.

Un rivenditore diventa tale quando l'owner autorizza il suo Telegram ID: chi
scrive senza permessi genera una notifica all'owner con **Autorizza**, oppure
l'owner lo collega a mano:

```bash
python -m app.cli reseller-set --name Mario --telegram-id 123456789
python -m app.cli resellers
python -m app.cli reseller-group --chat-id -1001234567890 --name Mario --title "Gruppo Mario"
```

Nel gruppo del rivenditore si usa `/collega` (lui) o `/collega Mario` (owner) e
`/scollega` per staccarlo. Nel gruppo valgono solo i comandi indirizzati
(`/scadenze@bot`): letture sì, modifiche no. Il
rivenditore vede le sue linee, non può cambiare prezzi o scadenze, e invia
richieste che l'owner approva con la durata che preferisce.

Job nel processo:

| Job | Quando | Cosa fa |
| --- | --- | --- |
| heartbeat | ogni 60 s | scrive `/data/heartbeat`, healthcheck Docker |
| reminder | 09:00 Europe/Rome | scadenze per owner e rivenditori, dedupe giornaliera |
| backup | 03:15 Europe/Rome | copia sqlite e retention (`BACKUP_RETENTION_DAYS`) |

Il reminder non ripete ogni giorno le linee già scadute: le segnala il primo
giorno dopo la scadenza, poi tornano solo se rinnovate e riscadute.

## Linee sparite ed eccezioni

Dopo una sync, `prune-missing` chiude le linee che non esistono più su nessun
pannello. È dry-run di default e cancella anche le righe orfane; una linea
viene chiusa solo se la sua scadenza locale è già passata e non resta nessuna
riga sul pannello.

```bash
# anteprima: cosa chiuderebbe e cosa lascia a decidere
python -m app.cli prune-missing

# applica (--force se le righe sparite superano il 30% di un pannello)
python -m app.cli prune-missing --apply [--force]
```

Le anomalie accettate (un pannello scaduto e l'altro attivo, una linea
personale, un errore noto) si nascondono dal report con un'eccezione. Con
`--until` l'anomalia torna a essere segnalata da quella data: la sync mostra
`⏰ eccezione da rivedere`.

```bash
python -m app.cli exceptions
python -m app.cli exception-set --line 78 --reason "decidere alla scadenza del VOD" --until 2026-12-19
python -m app.cli exception-remove --line 78
```

Senza `--until` l'eccezione vale per sempre (es. una linea personale).

Se un pannello non risponde, gli altri continuano: il report segna
`❌ piattaforma: errore` e il comando esce con codice 1.

| Sintomo | Diagnosi |
| --- | --- |
| `credenziali mancanti in .env` | compila `PANEL_*` e riavvia |
| `login rifiutato` | password cambiata o 2FA attivo |
| `ha risposto 401/403` dopo il login | sessione scaduta: cancella il file e riprova |
| timeout su LUCKY/VOD | host HTTP non raggiungibile: serve VPN o sync altrove |
| `username duplicato nel pannello` | due righe con lo stesso username: controlla il pannello |
| `VOD senza Live` | orfano voluto: resta in "da rivedere", non viene indovinato |

Sessioni e cookie stanno in `/data/panel_sessions/*.json` (600). Per forzare
un nuovo login:

```bash
docker exec -it eight-manager-bot rm -f /data/panel_sessions/live_hallo.json
```

Non copiare i file di sessione fuori dal server e non committarli.

## Ispezione del database

```bash
docker exec -it eight-manager-bot python - <<'PY'
import sqlite3
con = sqlite3.connect("/data/8manager.sqlite3")
for row in con.execute("select id, name from customers order by id limit 10"):
    print(row)
PY
```

Oppure copia il file in locale:

```bash
scp deployer@49.12.34.115:/home/deployer/8-manager/data/8manager.sqlite3 ./data/copia.sqlite3
```

## Rotazione del token del bot

1. BotFather → `/revoke` → nuovo token.
2. Aggiorna `BOT_TOKEN` in `.env`.
3. `./scripts/encrypt.sh`
4. `./scripts/deploy.sh --apply`

## Rotazione della chiave SSH

1. Genera una nuova chiave e autorizzala sul server (`authorized_keys`).
2. Sostituisci `secrets/ssh/HETZNER-personal-1/deployer.pub`.
3. Cifra la privata nel path previsto.
4. Verifica con `./scripts/verify.sh`.
5. Rimuovi la vecchia chiave da `authorized_keys`.

## Problemi comuni

| Sintomo | Diagnosi |
| --- | --- |
| `Connection refused` sulla 22 | VPN/instradamento o sshd sotto carico; riprova, non è la chiave. Se persiste, controlla la console Hetzner |
| Compose mostra `The "..." variable is not set` | manca `COMPOSE_DISABLE_ENV_FILE=1` o `env_file: raw` |
| `Permission denied (publickey)` | chiave revocata o `known_hosts` sbagliate |
| `bot unhealthy` | `verify.sh`, poi i log |
| `database is locked` | un altro processo scrive; controlla i container |
| disco pieno | `docker builder prune -f` |
| reminder non arrivati | fase D non ancora implementata |

## Healthcheck

Il container è healthy se:

- `/data/heartbeat` esiste ed è più recente di 180 secondi;
- il database SQLite risponde a `SELECT 1`.

L'heartbeat è scritto all'avvio e ogni 60 secondi dal JobQueue.

## Ripristino

Vedi [DEPLOYMENT.md](../DEPLOYMENT.md#backup-e-ripristino).
