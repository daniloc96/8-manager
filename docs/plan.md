# Piano di lavoro — 8-manager

Ultimo aggiornamento: **2026-11-02 (notte)**.
Questo file è il punto di partenza per chi riprende il lavoro: stato reale,
decisioni già prese, cosa manca e come si mette in produzione una modifica.

Per i dettagli tecnici: `docs/architecture.md`, `docs/data-model.md`,
`docs/panels-api.md`, `docs/bot-flows.md`, `docs/runbook.md`.

## Stato in una riga

Fasi **A/P/S** (infrastruttura, discovery, sync pannelli), **B** (clienti,
linee, prezzi, rinnovi, incassi), **C** (rivenditori e richieste) e **D**
(reminder e backup) sono implementate, testate e **in produzione** su
`bitch-0` come `@l7_manager_bot`. Il backfill commerciale è applicato.
Le rifiniture di interfaccia (modifica anagrafica, scheda cliente, wizard
completo, impostazioni modificabili, “non rinnova”) sono in produzione dal
2026-11-03. Resta la sola Fase F, rinviata di proposito (vedi “Cosa manca”). La VPS è censita in
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
| Ultimo deploy | `1a56998` · 2026-11-03T00:09Z · schema `4d2c9a1b7e38` · 216 test · CI verde |

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
  `/incassi` (rinnovi del mese per rivenditore **e valore del parco**: linee
  attive × prezzo mensile), `/status`, `/sync`,
  `/sync importa`, `/id`, `/help`;
- scheda linea: rinnovo 1/2/3/5/12 mesi o importo libero con **Annulla** per
  15 minuti, **Prezzo** modificabile, **Storico**, **Cliente**;
- **Modifica** anagrafica dalla scheda, un campo alla volta: alias del cliente,
  etichetta della linea, note, ID pannello e codice della riga di pannello.
  `-` svuota il campo; un'etichetta già usata da un'altra linea dello stesso
  cliente viene rifiutata senza perdere quello che si stava scrivendo;
- **scheda cliente**: tutte le linee attive con scadenza, prezzo e totale
  mensile; da ricerca compare un pulsante cliente quando un cliente ha più
  linee nei risultati. Il rivenditore vede solo le proprie linee e quante gliene
  restano nascoste;
- ricerca libera anche scrivendo senza comando (nome, alias, etichetta,
  username, ID);
- autorizzazione rivenditori: notifica all'owner con **Autorizza**, oppure
  `reseller-set` da CLI;
- gruppi rivenditore: `/collega` (rivenditore) o `/collega Mario` (owner),
  `/scollega`; nel gruppo solo comandi indirizzati, letture sì e modifiche no;
- `/richiedi` rivenditore: richiesta in privato, approvazione owner con la
  durata scelta, conferma al rivenditore e nel gruppo di origine;
- **wizard `/nuovo` completo**: nome, alias, servizio, rivenditore, etichetta,
  prezzo, durata, ID pannello e codice di accesso. Alias, etichetta e dati
  tecnici si possono saltare con **Salta**; il blocco finale da copiare nel
  pannello riporta anche quello che è stato inserito;
- **impostazioni modificabili**: `vod_domain` si cambia dal bot con
  validazione (schema `http`/`https`, niente percorso né parametri). `-`
  riporta la chiave al default di `.env`; un valore vuoto viene rifiutato;
- **Rivenditore da bot**: dalla scheda cliente «🏷 Rivenditore» sposta tutte le
  linee attive su un rivenditore o le riporta a «Diretto», con audit per linea;
  la stessa voce è nel menu **Modifica** della singola linea. Il campo vive sulla
  linea, quindi il sync non lo sovrascrive;
- **Vista Codici**: «🔑 Codici» dalla scheda cliente o dalla linea elenca per
  ogni pannello username, password, codice, ID e scadenza salvati dal sync. Solo
  owner. 8TV+ non ha password, il VOD non ha codice: la vista lo dice;
- **«Non rinnova» dal reminder**: ogni linea in scadenza ha il pulsante
  **🚫 Non rinnova**, con conferma prima di scrivere. La marcatura non tocca
  il pannello: è un promemoria che resta nel reminder delle 09:00 (anche se la
  linea è lontana dalla scadenza) e si vede sulla scheda. Si annulla con
  **↩️ Togli il segno** dal reminder o dalla scheda, oppure rinnovando la linea
  (il rinnovo azzera la marcatura con una voce in `audit_log`). Il reminder ha
  un pulsante anche per le linee marcate lontane dalla scadenza.

Job:

- reminder 09:00 all'owner (scadenze, linee marcate «non rinnovare» ed
  eccezioni da rivedere) e a ogni rivenditore con `telegram_user_id` e
  `notify`, solo le sue linee;
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

1. **Fase F**: rinnovo/cancellazione via HTTP sui pannelli. Rinviata di
   proposito: richiede gestione errori su sistemi non nostri. Gli adapter oggi
   espongono solo `fetch_lines`: il bot legge e non scrive mai sui pannelli.

Nota sulla modifica anagrafica: il **link VOD** non è un campo salvato, si
genera da `vod_domain` + username e password della linea (vedi
`docs/panels-api.md`). Per questo non compare nel menu **Modifica**: cambia
dominio, non cliente.

## Corretto il 2026-11-02 (notte)

- **Le viste del menu sollevavano `TypeError`**: `menu._target` era sincrona e
  restituiva un metodo legato, ma tutti e sei i chiamanti facevano
  `await _target(update)`. Risultato: `/start` da bottone, `/scadenze`,
  `/incassi`, `Rivenditori` e `Impostazioni` fallivano appena usati, con
  `object method can't be used in 'await' expression`. Non era emerso perché
  il deploy è recente e nessun handler era stato esercitato dopo il riavvio
  (i log mostravano solo heartbeat). Ora `_target` è `async` e
  `tests/test_bot_menu.py` copre ogni vista, sia da bottone sia da comando.

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
uv run pytest -q                         # 233 test al 2026-11-03
./scripts/deploy.sh --apply              # produzione
./scripts/verify.sh
```

Prima di un deploy: test verdi e lint pulito. Dopo: `verify.sh` e
`check`. Mai `docker compose down`, `prune` o `--remove-orphans` sull'host: ci
sono altri stack.

Migrazioni: `uv run alembic revision -m "…"`, poi il deploy applica
`upgrade head` da solo. Le migrazioni già applicate in produzione:
`fe398d565583` (schema iniziale) · `e077e6f8985d` (access code) ·
`1b68e6a3f7e5` (eccezioni) · `81f880a2f4a3` (gruppo rivenditore) ·
`4d2c9a1b7e38` (flag «non rinnovare»).
