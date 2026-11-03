# Architettura

## Vista d'insieme

```
Telegram  ──polling──▶  container eight-manager-bot
                          ├── python-telegram-bot (handler)
                          ├── JobQueue (heartbeat, reminder)
                          ├── SQLAlchemy async
                          ├── httpx ──▶ 8Plus / LUCKY LIVE / VOD
                          └── SQLite  /data/8manager.sqlite3
                                      └── /data/backups/
```

Nessuna porta pubblica, nessun webhook, nessun reverse proxy: il bot parla
verso l'API di Telegram in uscita. Per questo non tocca Traefik e non entra
nella rete `proxy`.

## Perché SQLite

Il carico è di poche scritture al giorno, un solo processo e un solo owner.
SQLite con WAL è più che sufficiente e elimina un servizio da aggiornare.
Il file è unico, il backup è una copia consistente con l'API `sqlite3.backup`,
e il ripristino è un `cp`. Se un domani servirà Postgres, SQLAlchemy e Alembic
sono già astratti: cambia la URL e il driver.

## Macchina a stati di una linea

Lo stato non è salvato: si calcola da `expires_on` rispetto a oggi.

```
expires_on < oggi        → scaduta
oggi ≤ expires_on ≤ +3g  → in scadenza
altrimenti               → attiva
```

`anchor_day` conserva il giorno del mese del periodo. Serve a non far derivare
le scadenze di fine mese: 31/01 → 28/02 → 31/03, non 28/03.

## Modello a due livelli

- **Commerciale** (nel bot): cliente, alias, rivenditore, etichetta, prezzo,
  storico rinnovi.
- **Operativo** (nei pannelli): credenziali, scadenza reale, stato, pacchetto.

`panel_lines` è il ponte: una riga per identità esterna, con `line_id` nullo
finché non viene classificata. Una linea Live+VOD può avere due righe (live e
VOD); una linea solo Live ne ha una.

## JobQueue

- **heartbeat** ogni 60 secondi: scrive `/data/heartbeat`, usato
  dall'healthcheck Docker. Se il processo è vivo ma il polling è bloccato,
  l'heartbeat si ferma e il container diventa unhealthy.
- **reminder** quotidiano (fase D): scadenze, scadute, invio ai rivenditori.
- **backup** notturno (fase D): API sqlite3 e retention.

I job vivono nel processo: un riavvio li ricrea. La deduplica degli inviti è
nella tabella `notifications`, con vincolo unico per tipo, linea, chat e giorno.

## Flusso di deploy

```
locale                          server
──────                          ──────
.env + .env.enc
sops decrypt ──┐
chiave SSH  ───┼──▶ rsync ────▶ /home/deployer/8-manager
               │                  ├── .env (600)
               │                  ├── data/ (volume)
               │                  └── docker compose
               └──▶ ssh ────────▶ build → migrate → up -d → health
```

## Sync pannelli

Il sync è diviso in due fasi, così l'accoppiamento Live↔VOD funziona anche
sincronizzando una piattaforma alla volta:

1. **Fotografia** — per ogni adapter si leggono le righe e si fa upsert in
   `panel_lines` per `(platform, username_key)`. I collegamenti esistenti non
   vengono toccati; le righe sparite restano e vengono segnalate.
2. **Classificazione** — si accoppiano LUCKY LIVE e VOD per username
   normalizzato (la nota deve essere compatibile), si importano le righe non
   collegate, si assegna l'etichetta alle linee multiple e si allinea
   `lines.expires_on` alla scadenza del pannello. Per Live+VOD vince la
   scadenza più corta; una differenza tra i due pannelli resta "da rivedere".

L'import crea cliente e linea con prezzo `0` e nota "prezzo da impostare":
la parte commerciale arriva dalla fase B. Gli orfani (VOD senza Live, note
incompatibili, nomi mancanti) non vengono indovinati: finiscono nel report.

Per LUCKY LIVE e VOD il sync legge anche `/lines/meta` e salva in `settings`
`live_domain` e `vod_domain`: sono globali, cambiano solo se il provider cambia
host, e servono a costruire i link della scheda con le credenziali della
singola riga. 8Plus non espone un dominio di download.

`sync_runs` registra una riga per piattaforma con i conteggi; `audit_log`
registra import, correzioni di scadenza e domini aggiornati. Le sessioni HTTP
sono in `/data/panel_sessions/` (600) e non stanno nel database.

## Scelte rinviate

- **Auto-rinnovo via HTTP**: fase F, solo se il sync conferma la stabilità.
- **Accesso rivenditori**: fase C, con allowlist su Telegram ID.
- **Reminder**: fase D, con deduplica giornaliera.
