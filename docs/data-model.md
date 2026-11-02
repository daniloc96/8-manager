# Modello dati

## Tabelle

### `customers`

| Campo | Note |
| --- | --- |
| `id` | |
| `name` | nome della persona |
| `alias` | soprannome breve, entra nella ricerca |
| `search_key` | `name + alias` normalizzati (minuscolo, senza accenti) |
| `notes` | note libere |
| `deleted_at` | soft delete |

### `resellers`

| Campo | Note |
| --- | --- |
| `name` / `name_key` | nome unico, normalizzato |
| `telegram_user_id` | unico, null finché non autorizzato |
| `default_monthly_price_cents` | prezzo proposto per le nuove linee |
| `notify` | riceve i reminder |
| `active` | |

### `lines`

| Campo | Note |
| --- | --- |
| `customer_id` | FK, `RESTRICT` |
| `label` | "Linea 1", "Linea 2"; unico per cliente tra le attive |
| `reseller_id` | null = cliente diretto |
| `service` | `live` o `live_vod` |
| `monthly_price_cents` | quello che deve incassare l'owner |
| `started_on` | inizio del periodo corrente |
| `expires_on` | scadenza |
| `anchor_day` | giorno del mese, 1–31 |
| `deleted_at` | soft delete |

### `panel_lines`

| Campo | Note |
| --- | --- |
| `line_id` | null = da classificare |
| `platform` | `live_hallo`, `vod_newvod`, `live_8tvplus` |
| `external_id` | ID nel pannello |
| `username` / `username_key` | credenziale, unica per piattaforma |
| `password` | credenziale |
| `access_code` | codice aggiuntivo (8Plus: coincide con `code`; VOD può nasconderlo) |
| `expires_on` / `status` / `package` | fotografia del pannello |
| `note` | campo note del pannello |
| `raw_json` | payload originale, per debugging |
| `first_seen_at` / `last_seen_at` | |

Vincoli: `(platform, username_key)` unico; `(line_id, platform)` unico tra le
righe collegate.

Il sync aggiorna queste righe a ogni esecuzione. Una riga che sparisce dal
pannello non viene cancellata: resta con `last_seen_at` vecchio e finisce nel
report. `line_id` nullo significa "da classificare": può essere un orfano
(VOD senza Live, nota incompatibile) o una riga appena importata.

L'import crea linee con `monthly_price_cents = 0` e nota "prezzo da
impostare": il prezzo è un dato commerciale, non arriva dal pannello.

### `renewals`

| Campo | Note |
| --- | --- |
| `line_id` | |
| `months` | 1–36 |
| `monthly_price_cents` | snapshot |
| `total_cents` | default prezzo per mesi, correggibile |
| `period_start` / `period_end` | |
| `source` | reminder, card, request, sync, manual, import |
| `actor_telegram_id` | chi ha registrato |

Nessuno stato pagato/non pagato: il totale è informativo.

### Altre

- `renewal_requests`: richieste dei rivenditori con stato e chat di origine.
- `notifications`: deduplica dei reminder.
- `audit_log`: modifiche manuali.
- `settings`: valori modificabili dal bot (`vod_domain`).
- `sync_runs`: esito dei sync dei pannelli.

## Calcolo dei mesi

```python
add_months(base, months, anchor_day)
```

- scade 13/11, rinnovo 01/11 per 2 mesi → 13/01
- scaduta 13/10, rinnovo 01/11 per 1 mese → 01/12
- 31/01 + 1 → 28/02; con `anchor_day=31`, + 1 ancora → 31/03

`months_between(start, end)` serve al sync: se la scadenza salta da 13/11 a
13/02, il rinnovo è di 3 mesi. Se il salto non è un numero intero di mesi,
il sync segnala la linea per revisione invece di indovinare.

## Ricerca

`normalize_key` toglie accenti, minuscole e spazi doppi. La ricerca unisce
nome, alias, etichetta della linea, username e ID del pannello. Chi scrive
"Mario Caterino 2" trova la linea con quell'etichetta anche se il cliente
è uno solo con due linee.
