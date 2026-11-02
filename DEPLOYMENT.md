# Deploy

## Host

| Campo | Valore |
| --- | --- |
| Provider | Hetzner |
| Hostname | `bitch-0` |
| IPv4 | `49.12.34.115` |
| OS | Ubuntu 24.04.4 LTS |
| Docker | 29.2.1, Compose v5.0.2 |
| Utente | `deployer`, sudo NOPASSWD |
| Path | `/home/deployer/8-manager` |
| Container | `eight-manager-bot` |
| Compose project | `eight-manager` |

La macchina ospita anche `bot-prod` (bot Amazon), `keepa-proxy`,
`guvarda-prod`, `nutrition-day`, `dott-luciano-mottola`, `private-traccar`,
Traefik e Portainer.

**Regole**: mai `docker compose down`, `prune`, `--remove-orphans` o comandi
che tocchino gli altri stack. Il deploy agisce solo su `eight-manager`.

## Segreti

| File | In git | Contenuto |
| --- | --- | --- |
| `.env` | no, gitignored | token, owner, configurazione |
| `.env.enc` | sì | `.env` cifrato con age |
| `.sops/keys.txt` | no, gitignored | identità age di flotta |
| `secrets/ssh/HETZNER-personal-1/deployer.enc` | sì | chiave SSH cifrata |
| `secrets/ssh/HETZNER-personal-1/deployer.pub` | sì | chiave pubblica |
| `secrets/ssh/HETZNER-personal-1/known_hosts` | sì | host key pinnate |

L'identità age è la stessa di tutta la flotta: si copia da un altro repository
in `.sops/keys.txt` (0600), oppure si mette in `~/.config/sops/age/keys.txt`.

```bash
cp /path/to/altro-repo/.sops/keys.txt .sops/keys.txt
chmod 600 .sops/keys.txt
```

## Primo deploy

1. Compila `.env`:

   ```bash
   cp .env.example .env
   chmod 600 .env
   # BOT_TOKEN e OWNER_TELEGRAM_ID sono obbligatori
   ```

2. Cifra:

   ```bash
   ./scripts/encrypt.sh
   ```

3. Verifica senza scrivere:

   ```bash
   ./scripts/deploy.sh --dry-run
   ```

4. Applica:

   ```bash
   ./scripts/deploy.sh --apply
   ```

5. Controlla:

   ```bash
   ./scripts/verify.sh
   ```

## Cosa fa `deploy.sh --apply`

1. decifra `.env.enc` e la chiave SSH in una directory temporanea (700);
2. controlla token e owner, spazio libero (minimo 2 GB), Docker Compose;
3. sincronizza i sorgenti con `rsync` (esclude `.env`, `data`, `.sops`,
   `secrets`, `.git`);
4. scrive `.env` sul server con permessi `600`;
5. `docker compose build bot`;
6. `docker compose run --rm --no-deps bot alembic upgrade head`;
7. `docker compose up -d bot`;
8. attende l'healthcheck e stampa stato e log.

Se `.env` è più recente di `.env.enc`, il deploy ri-cifra prima di partire.

## Segreti con `$`

`.env` può contenere password con `$` (per esempio `PANEL_8TVPLUS_PASS`).
Per non farle interpretare a Compose:

- `docker-compose.yml` e `docker-compose.prod.yaml` usano
  `env_file: {path: .env, format: raw}`;
- i comandi `docker compose` lanciati dagli script prefissano
  `COMPOSE_DISABLE_ENV_FILE=1`.

Senza queste due impostazioni il container riceve una password troncata e i
log mostrano un warning con un pezzo del segreto. Non rimuoverle.
Richiede Docker Compose >= 2.30 (sulla VPS c'è la v5.x).

## Rollback

Il deploy non usa git sul server: per tornare indietro si rilancia lo script
dal commit precedente.

```bash
git checkout <commit-precedente>
./scripts/deploy.sh --apply
```

Il database non viene toccato dal rollback. Se la migrazione ha cambiato lo
schema, ripristina il backup più recente **prima** di riavviare la versione
vecchia (vedi sotto).

## Backup e ripristino

Backup manuale:

```bash
./scripts/backup.sh
```

I file finiscono in `/home/deployer/8-manager/data/backups/` con permessi 600;
la retention è `BACKUP_RETENTION_DAYS` (default 30).

Ripristino (da eseguire con il bot fermo):

```bash
ssh deployer@49.12.34.115
cd /home/deployer/8-manager
docker compose -f docker-compose.prod.yaml stop bot
cp data/8manager.sqlite3 data/8manager.sqlite3.before-restore
cp data/backups/8manager-YYYYMMDD-HHMMSS.sqlite3 data/8manager.sqlite3
chmod 600 data/8manager.sqlite3
COMPOSE_DISABLE_ENV_FILE=1 docker compose -f docker-compose.prod.yaml start bot
```

## Disco

La VPS aveva circa 5,9 GB liberi su 38 GB e **14 GB di build cache Docker**.
Il prune del 2026-11-02 ha portato il disco a circa 20 GB liberi. Il preflight
del deploy si ferma sotto i 2 GB liberi. Per recuperare spazio senza toccare
immagini o container:

```bash
ssh deployer@49.12.34.115 'docker builder prune -f'
```

Non usare `docker system prune` né `docker image prune` alla cieca: sulla
macchina girano altri stack.

## Troubleshooting

| Sintomo | Cosa controllare |
| --- | --- |
| `Connection refused` sulla 22 | VPN/instradamento o sshd sotto carico: riprova; non è la chiave |
| `The "..." variable is not set` da Compose | manca `COMPOSE_DISABLE_ENV_FILE=1` o `env_file: raw` |
| `bot unhealthy` | `./scripts/verify.sh` e i log del container |
| `BOT_TOKEN mancante` | `.env` non compilato o `.env.enc` vecchio |
| `no matching creation rules` | stai cifrando un file fuori dal repo: copialo nel path previsto |
| disco pieno | `docker builder prune -f` e ricontrolla |
