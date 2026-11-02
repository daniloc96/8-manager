# Piano di lavoro — 8-manager

Ultimo aggiornamento: **2026-11-03 (notte)**.
Questo file è il punto di partenza per chi riprende il lavoro: stato reale,
decisioni già prese, cosa manca e come si mette in produzione una modifica.

Per i dettagli tecnici: `docs/architecture.md`, `docs/data-model.md`,
`docs/panels-api.md`, `docs/bot-flows.md`, `docs/runbook.md`.

## Stato in una riga

Fasi **A/P/S** (infrastruttura, discovery, sync pannelli), **B** (clienti,
linee, prezzi, rinnovi, incassi), **C** (rivenditori e richieste) e **D**
(reminder e backup) sono implementate, testate e **in produzione** su
`bitch-0` come `@l7_manager_bot`. Il backfill commerciale è applicato.
Restano rifiniture di interfaccia (vedi “Cosa manca”). La VPS è censita in
`vps-infrastructure` come `HETZNER-personal-1` e le tre repo di lavoro sono
allineate ai rispettivi remoti.

## Produzione

| Campo | Valore |
| --- | --- |
| Host | `49.12.34.115` (`bitch-0`, Hetzner) |
| Utente / path | `deployer` · `/home/deployer/8-manager` |
| Container | `eight-manager-bot` (healthy), compose project `eight-manager` |
| Bot | `@l7_manager_bot`, polling, nessuna porta pubblica |
| DB | `/data/8manager.sqlite3` (SQLite WAL), backup in `/data/backups` |
| Job | heartbeat 60 s · reminder 09:00 · backup 03:15 (Europe/Rome) |

```bash
./scripts/deploy.sh            # dry-run
./scripts/deploy.sh --apply    # build, migrazioni, restart, healthcheck
./scripts/verify.sh            # compose, health, disco, log
./scripts/backup.sh            # copia sqlite con retention
```

Il deploy usa sempre **bash**, non zsh: `scripts/lib/remote.sh` ha un `trap`
che in zsh cancella la chiave SSH temporanea prima di usarla.

## Dati (2026-11-02)

| Voce | Valore |
| --- | --- |
| Clienti attivi | 56 (84 totali, i chiusi restano) |
| Linee attive | 57 (86 totali) |
| Live+VOD / solo Live | 47 / 10 |
| Rivenditori | 1: **Mario**, senza ID Telegram collegato |
| Linee di Mario | 35 · 235 €/mese |
| Linee dirette | 22 · 161 €/mese |
| Totale parco | **396 €/mese** (3 linee a 0 €: Personale, Alessia, Danilo) |
| Eccezioni attive | linea 33 (personale, per sempre) · 78 (fino al 19/12/2026) · 11 (fino al 04/01/2027) |

Regole commerciali applicate nel backfill:

- rivenditore Mario: **7 €** Live+VOD, **6 €** solo Live, **4 €** eccezioni
  (linea 9 `Mario Principale`, linea 70 `Nicola fratello Mario`);
- diretti: 10 € (12 linee), 8 € (6, 10), 5 € (32, 56, 57, 59, 80), 0 € (8, 11, 33).

## Infrastruttura censita

`daniloc96/vps-infrastructure`, cartella `HETZNER-personal-1`: identità della
macchina, chiave `deployer` cifrata e replicata, stack Traefik/Portainer
documentato come **manuale** (`VPS_STACK_MANAGED=false`) e otto domini dei
progetti. `./scripts/verify.sh HETZNER-personal-1` passa (container, porte
80/443, ACME, probe HTTPS, redirect).

## Come funziona il ciclo

**I pannelli sono la verità, il bot è lo specchio.** Il bot non scrive mai su
8Plus, Lucky o VOD: legge, confronta e registra.

1. l'operazione (rinnovo, nuova linea, cancellazione) si fa **nel pannello**;
2. `/sync` dal bot (solo in privato, solo owner) o `app.cli sync`;
3. il sync aggiorna le scadenze, registra i rinnovi a mesi interi e segnala le
   anomalie non spiegabili;
4. le anomalie accettate si nascondono con `exception-set`;
5. le linee cancellate in remoto si chiudono con `prune-missing`: una linea
   esce dal bot **solo** quando tutte le sue righe sono sparite dai pannelli.

Non esiste un pulsante “Disattiva”: la cancellazione remota è l'unica fonte di
verità. `prune-missing` è dry-run di default e si blocca se le righe sparite
superano il 30% di un pannello (`--force` per procedere): un pannello che
risponde vuoto non deve svuotare l'archivio.

## Implementato

Bot (owner):

- `/start` menu con tastiera, `/scadenze` (scadute, oggi, prossimi 3 giorni),
  `/cerca`, `/nuovo` (wizard cliente+linea con credenziali da copiare),
  `/incassi` (rinnovi del mese per rivenditore), `/status`, `/sync`,
  `/sync importa`, `/id`, `/help`;
- scheda linea: rinnovo 1/2/3/5/12 mesi o importo libero con **Annulla** per
  15 minuti, **Prezzo** modificabile, **Storico**;
- ricerca libera anche scrivendo senza comando (nome, alias, etichetta,
  username, ID);
- autorizzazione rivenditori: notifica all'owner con **Autorizza**, oppure
  `reseller-set` da CLI;
- gruppi rivenditore: `/collega` (rivenditore) o `/collega Mario` (owner),
  `/scollega`; nel gruppo solo comandi indirizzati, letture sì e modifiche no;
- `/richiedi` rivenditore: richiesta in privato, approvazione owner con la
  durata scelta, conferma al rivenditore e nel gruppo di origine.

Job:

- reminder 09:00 all'owner (scadenze + eccezioni da rivedere) e a ogni
  rivenditore con `telegram_user_id` e `notify`, solo le sue linee;
- deduplica per `(tipo, linea, chat, giorno)` in `notifications`;
- una linea già scaduta viene segnalata il primo giorno dopo la scadenza, non
  ogni giorno;
- backup notturno 03:15 con retention `BACKUP_RETENTION_DAYS`.

CLI (`docker exec eight-manager-bot python -m app.cli …`):

| Comando | Uso |
| --- | --- |
| `sync [--dry-run] [--import] [--platform]` | riconcilia i pannelli |
| `prune-missing [--apply] [--force]` | chiude le linee sparite, pulisce le righe orfane |
| `exceptions` · `exception-set` · `exception-remove` | anomalie accettate |
| `resellers` · `reseller-set` · `reseller-group` | anagrafica rivenditori |
| `line-set` · `customer-rename` · `line-restore` | correzioni puntuali |
| `backfill --file CSV [--apply]` | prezzi e rivenditori in blocco |
| `check` · `backup` · `migrate` | manutenzione |

## Cosa manca

In ordine di valore. Nessuna di queste è necessaria perché il bot funzioni
oggi: sono rifiniture che riducono il lavoro manuale.

1. **Modifica anagrafica** dalla scheda: alias, etichetta (“Linea 1/2”), note e
   dati tecnici (ID pannello, codice live, link VOD). Oggi si cambia solo il
   prezzo. Servono le callback e un campo alla volta, con `access.can_act`.
2. **Vista cliente**: un cliente con più linee (es. Mario Caterino 1/2/VOD) non
   ha una scheda unica con totale mensile; i risultati di ricerca sono linee.
3. **Wizard `/nuovo` completo**: oggi chiede nome, servizio, rivenditore,
   prezzo e durata; mancano alias, etichetta e dati tecnici del pannello.
4. **“Non rinnova” dal reminder**: la specifica lo prevede sul messaggio delle
   09:00. Da decidere *cosa* deve fare ora che la disattivazione manuale non
   esiste: l'unica azione sensata è segnare la linea come “non rinnovare” e
   aspettare che scada e sparisca dal pannello.
5. **Impostazioni modificabili**: `settings` oggi è in sola lettura
   (`vod_domain` si legge solo dal wizard).
6. **Valore del parco** in `/incassi`: riga “linee attive × prezzo” accanto ai
   rinnovi del mese (396 €/mese al 2026-11-02).
7. **Fase F**: rinnovo/cancellazione via HTTP sui pannelli. Rinviata di
   proposito: richiede gestione errori su sistemi non nostri.

Debito tecnico noto:

- il repo è **pubblico** su GitHub: i segreti sono cifrati con SOPS/age
  (`.env.enc`, `secrets/ssh/*.enc`) e la chiave privata non è versionata, ma IP
  e URL dei pannelli sono visibili. Se non serve pubblico: `gh repo edit
  --visibility private`;
- la CI (`/.github/workflows/ci.yml`) esiste ma gira solo dopo il primo push;
- `docs/handoff-2026-11-02.md` è la fotografia della mattina: superato da
  questo file.

## Come si lavora

```bash
uv sync                                  # dipendenze
uv run ruff check . && uv run ruff format --check .
uv run pytest -q                         # 145 test al 2026-11-02
./scripts/deploy.sh --apply              # produzione
./scripts/verify.sh
```

Prima di un deploy: test verdi e lint pulito. Dopo: `verify.sh` e
`check`. Mai `docker compose down`, `prune` o `--remove-orphans` sull'host: ci
sono altri stack.

Migrazioni: `uv run alembic revision -m "…"`, poi il deploy applica
`upgrade head` da solo. Le migrazioni già applicate in produzione:
`fe398d565583` (schema iniziale) · `e077e6f8985d` (access code) ·
`1b68e6a3f7e5` (eccezioni) · `81f880a2f4a3` (gruppo rivenditore).
