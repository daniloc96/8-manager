# Pannelli: API e integrazione

Discovery eseguita il **2026-11-02** in sola lettura, con login manuale
dell'owner nella finestra Playwright. Nessuna scrittura sui pannelli.

Snapshot completo in `data/` (gitignored, permessi 600):

| File | Contenuto |
| --- | --- |
| `data/panel-8plus-2026-11-02.json` | 10 linee solo Live |
| `data/panel-lucky-live-2026-11-02.json` | 76 linee Live del pacchetto Live+VOD |
| `data/panel-vod-2026-11-02.json` | 90 linee VOD |
| `data/panel-lines-2026-11-02.csv` | vista unica con nome, scadenza, stato, accoppiamento |

Totale: **176 linee**. I file contengono credenziali: non entrano in git.

## Piattaforme

| Piattaforma | Uso | Base URL | Auth |
| --- | --- | --- | --- |
| 8Plus | Solo Live | `https://my.8tvplus.com` | JWT Bearer, 1 ora + refresh |
| LUCKY LIVE | Live di Live+VOD | `http://netflix.oldadventure.org:25000` | cookie di sessione + CSRF |
| VOD | VOD di Live+VOD | `http://listavod.gostreet.org:2424` | cookie di sessione + CSRF |

LUCKY LIVE e VOD sono **la stessa applicazione** (stessi endpoint, stesso
schema). Cambiano brand, dati e crediti.

## 8Plus (solo Live)

### Login

```
POST /api/auth/login
{"email": "...", "password": "..."}
→ {"access_token": "...", "refresh_token": "...", "role": "reseller", "email": "..."}
```

- token in `Authorization: Bearer <access_token>`; scade dopo 1 ora;
- refresh: `POST /api/auth/refresh {"refresh_token": "..."}` → nuovo access;
- 2FA supportato (`totp_required`, `pre_2fa_token`) ma sull'account attuale
  `GET /api/me` riporta `totp_enabled: false`.

### Letture

| Endpoint | Risposta |
| --- | --- |
| `GET /api/me` | utente, `credit_balance`, `totp_enabled` |
| `GET /api/reseller/dashboard` | conteggi e attività |
| `GET /api/reseller/codes?q=&status=&offset=&limit=` | `{rows:[...], total}` |
| `GET /api/reseller/pricing` | durate 30/90/180/365 giorni e costo in crediti |
| `GET /api/reseller/packs` | packs (vuoto sull'account attuale) |

Campi di una linea:

```json
{
  "id": "uuid",
  "code": "NPN8UDB86EAT",
  "max_connections": 1,
  "status": "active",
  "note": "Giovanni Pinetamare",
  "duration_days": 30,
  "credits_spent": 1,
  "expires_at": "2026-11-12T16:30:20+00:00",
  "created_at": "2026-10-13T16:30:20+00:00",
  "packs": [],
  "online_count": 0,
  "total_devices": 1
}
```

`note` è il nome del cliente. `duration_days` è la durata nominale del codice
creato, non il residuo.

### Scritture (non usate in v1)

| Endpoint | Body |
| --- | --- |
| `POST /api/reseller/codes` | `{duration_days, max_connections, pack_ids, note}` |
| `PUT /api/reseller/codes/{id}` | `{note, pack_ids, status?}` (nota gratis) |
| `POST /api/reseller/codes/{id}/renew` | `{duration_days}` (scala crediti) |
| `POST /api/reseller/codes/{id}/regenerate` | — |
| `POST /api/reseller/codes/{id}/suspend` | — |
| `DELETE /api/reseller/codes/{id}` | — |

**Durate disponibili: 30 / 90 / 180 / 365 giorni.** Non esistono 2 o 5 mesi.
Il rinnovo aggiunge giorni, non mesi di calendario: il bot deve adottare la
scadenza restituita dal pannello dopo ogni rinnovo.

## LUCKY LIVE e VOD

### Login

```
GET  /api/v1/auth/login              → {csrf, ...}
POST /api/v1/auth/login
     X-CSRF-Token: <csrf>
     {"username": "...", "password": "...", "referrer": "/"}
     → set-cookie di sessione
GET  /api/v1/session                 → {user, csrf}
```

Il campo `username` accetta l'email usata per il login. Ogni POST richiede
`X-CSRF-Token`; il token si legge da `/api/v1/session` (`data.csrf`). Le
chiamate successive usano il cookie jar, senza header di autorizzazione.

### Letture

| Endpoint | Risposta |
| --- | --- |
| `GET /api/v1/lines?page=&perPage=&sort=&dir=&q=&status=` | `{ok, data:{data:[...], total, page, perPage}}` |
| `GET /api/v1/lines/meta` | paginazione, azioni, `download.base_url` |
| `GET /api/v1/lines/editor?id=...` | pacchetti, bouquet, dettaglio linea |
| `GET /api/v1/recent-activity?page=&perPage=` | attività, incluse le `renew` |
| `GET /api/v1/reseller-packages` | pacchetti custom (vuoto) |

Campi di una linea:

```json
{
  "id": 512148,
  "member_id": 1782,
  "owner_name": "...",
  "username": "paoloc111026",
  "password": "...",
  "access_code": "KSUQ678257",
  "exp_date": 1799671139,
  "status": "active",
  "is_trial": false,
  "enabled": true,
  "max_connections": 1,
  "active_connections": 0,
  "notes": "Paolo Consiglio Mario",
  "tags": []
}
```

- `notes` = nome cliente;
- `exp_date` = Unix timestamp in secondi;
- `access_code` può essere `null` sul VOD (`hide_access_code: true`);
- `id` è numerico e stabile, diverso tra LUCKY e VOD.

Dettaglio (`/lines/editor?id=...`) aggiunge: `bouquet`, `playlist`,
`reseller_notes`, `allowed_ips`, `allowed_ua`, `is_mag`, `is_e2`,
`mac_address_mag`, `mac_address_e2`, `vpn`.

### Pacchetti di rinnovo

`GET /api/v1/lines/editor` restituisce `packages` (52 sul VOD, 94 sul Live).
I nomi seguono lo schema:

- VOD: `1 MESE (MONTH) - (All Package)`, `2 MESI`, `3 MESI`, `5 MESI`,
  `12 MESI`, più varianti `No Hot Club`, `XXX + LISTA RIDOTTA`, `Multivision`.
- LIVE: `1 Month / 1 Mese` … `12 Month / 12 Mesi`, più varianti `+ HOT`,
  `Multivision`, `Multivision ITALIA`.

Mappa dei pacchetti standard:

| Mesi | VOD `id` | Live `id` |
| --- | --- | --- |
| 1 | 41 | 5 |
| 2 | 83 | 6 |
| 3 | 42 | 7 |
| 5 | 85 | 9 |
| 12 | 44 | 16 |

Gli `id` possono cambiare: l'adapter deve risolverli **per nome**, con questi
valori solo come fallback.

### Rinnovo

```
POST /api/v1/renewals/preview
X-CSRF-Token: <csrf>
{"lines": [408946], "package": 42}

→ {
    "package": {"id": 42, "name": "3 MESI (MONTH) - (All Package)",
                "cost": 3, "duration": 3, "duration_in": "months",
                "max_connections": 1},
    "rows": [{"id": 408946, "username": "...", "exp_date": 1799671148,
              "new_exp": 1807443548, "max_connections": 1,
              "ok": true, "reason": null}],
    "count": 1, "total": 3, "credits": 102, "remaining": 99
  }
```

```
POST /api/v1/renewals
X-CSRF-Token: <csrf>
{"lines": [408946], "package": 42, "total": 3}
→ {results: [{id, status, ...}], credits: ...}
```

- `preview` è read-only: calcola costo, nuova scadenza e crediti residui;
- `lines` è un array: il pannello supporta il rinnovo multiplo;
- il costo è in crediti del pannello (VOD e Live hanno saldi separati);
- la nuova scadenza è calcolata dal pannello, con mesi di calendario
  (il salto di 3 mesi attraversa l'ora legale e perde un'ora in timestamp,
  ma la data resta corretta).

### Storico rinnovi

`GET /api/v1/recent-activity?page=N&perPage=25` restituisce eventi con:

```json
{
  "category": "renew",
  "label": "Extend Line",
  "text": "with Package [12 MESI (MONTH) - (All Package)]",
  "credits": {"before": 114, "after": 102},
  "date": 1793556731,
  "line_id": 408946
}
```

Il nome del pacchetto nel `text` contiene i mesi; `credits.before - after` è
il costo. È la fonte per importare lo storico dei rinnovi. L'API pagina a 25
righe anche se si chiede `perPage` più grande.

### Link Live e VOD

```
{download.base_url}/get.php?username={username}&password={password}&type=m3u_plus&output=mpegts
```

- `download.base_url` del VOD: `http://listavod.gostreet.org:24000`;
- `download.base_url` del Live: `http://hulu.oldadventure.org:23000`;
- il formato `m3u_plus` è quello usato dal pannello per il download;
- la password è quella della riga del pannello: Live e VOD possono averla
  diversa anche per lo stesso `username`.

Il dominio è l'unica parte variabile: il suffisso si genera dalle credenziali
della linea. Il sync legge `data.download.base_url` da `/lines/meta` e lo salva
in `settings` come `live_domain` e `vod_domain`: se il provider cambia host il
valore si aggiorna alla sync successiva. Le due chiavi restano modificabili dal
bot, ma la sync le riallinea al valore reale del pannello. 8Plus non espone un
dominio di download: le sue linee ricevono solo il codice.

## Accoppiamento Live ↔ VOD

- 76 linee Live, 90 VOD;
- **71 linee Live hanno lo stesso `username` di una linea VOD**;
- 69 hanno anche la stessa `note`;
- 63 coppie hanno la stessa scadenza, 7 hanno il VOD più avanti (fino a
  +702 giorni, probabilmente linee legacy), 1 il VOD 2 giorni prima;
- 5 linee Live non hanno VOD, 19 VOD non hanno Live.

Regola di import: accoppia per `username` normalizzato, verifica la `note`,
e metti le eccezioni in una lista "da rivedere". Non accoppiare mai per nome
solo: due clienti diversi possono chiamarsi allo stesso modo.

## Rete e sicurezza

- LUCKY LIVE e VOD sono su **HTTP**, senza TLS: credenziali, cookie di
  sessione e link viaggiano in chiaro. Usarli solo da rete fidati/VPN.
  Da verificare dalla VPS prima della fase S: se il server non li raggiunge,
  serve una VPN sul server o un sync altrove.
- 8Plus è su HTTPS (Cloudflare) e non ha problemi di rete.
- 2FA: 8Plus spento sull'account attuale; LUCKY/VOD espone `/account/2fa`,
  ma il login attuale non lo richiede. Se verrà attivato, serve un segreto
  TOTP nell'env.
- Sessioni: salva i cookie/JWT in `/data/panel_sessions/` con permessi 600,
  mai in git. Rinnova al 401 e avvisa l'owner se il login fallisce.
- Gli snapshot in `data/` contengono username e password delle linee: non
  committarli, non allegarli, cancellali quando la fase S è stabile.

## Implementazione (fase S)

| Modulo | Contenuto |
| --- | --- |
| `app/panels/base.py` | DTO `PanelLine`, errori, conversioni date |
| `app/panels/eightplus.py` | JWT, refresh, paginazione `/reseller/codes` |
| `app/panels/lucky.py` | cookie + CSRF, linee, pacchetti (`/lines/editor`) |
| `app/panels/packages.py` | mesi dal nome, preferenza pacchetto standard, durate 8Plus |
| `app/panels/sync.py` | upsert, accoppiamento, import, riconciliazione |
| `app/panels/report.py` | report per CLI e bot |
| `app/panels/snapshots.py` | adapter offline dagli snapshot |

Comandi:

```bash
python -m app.cli sync --dry-run
python -m app.cli sync
python -m app.cli sync --import
python -m app.cli sync --snapshot data --import
```

Regole applicate:

- accoppiamento Live↔VOD solo per `username` normalizzato, con nota
  compatibile (uguale o un insieme di parole contenuto nell'altro);
- VOD senza Live, note incompatibili e nomi mancanti restano in "da rivedere";
- la scadenza della linea segue il pannello: per Live+VOD vince la più corta;
  una differenza tra i due pannelli viene segnalata;
- l'import non inventa prezzi: crea la linea a `0` con nota "prezzo da
  impostare";
- le sessioni stanno in `/data/panel_sessions/*.json` (600), non nel DB.

8Plus non ha un vero `username`: si usa `code` come identificatore della
riga, e lo stesso valore viene salvato in `access_code`.

Verifica live del **2026-11-02** da rete locale: i tre adapter hanno letto
10 righe da 8Plus, 76 da LUCKY LIVE e 90 da VOD via `httpx`, in sola lettura.
8Plus non è stato bloccato da Cloudflare; LUCKY/VOD hanno completato login
cookie + CSRF. Resta da verificare la raggiungibilità degli host HTTP **dalla
VPS**, dove girerà il container.

## Verdetto

| Pannello | Verdetto | Conseguenza |
| --- | --- | --- |
| 8Plus | **API pulita** | sync e rinnovo via httpx |
| LUCKY LIVE | **API pulita** (cookie + CSRF) | sync e rinnovo via httpx |
| VOD | **API pulita** (cookie + CSRF) | sync e rinnovo via httpx |

Playwright non serve in produzione: è servito solo per il login manuale e per
leggere il traffico. La fase S può usare `httpx` con cookie jar e CSRF.

Il rinnovo automatico (fase F) è **fattibile**:

- 8Plus: una chiamata, `duration_days` 30/90/180/365;
- LUCKY+VOD: due chiamate, una per pannello, con preview obbligatoria prima
  del commit;
- gestione del fallimento parziale: se il Live riesce e il VOD no, la linea
  resta incoerente e va segnalata, non ritentata alla cieca.

Resta valida la scelta di prodotto: **v1 manuale con il bottone "Fatto"**.
L'auto-rinnovo è un'opzione, non un prerequisito.
