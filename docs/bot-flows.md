# Flussi del bot

Riferimento di progetto. **Implementato** nelle fasi B–D: menu, scadenze,
ricerca, scheda linea, rinnovo con undo, prezzo, storico,
incassi, registro rivenditori, wizard `/nuovo`, richieste di rinnovo,
autorizzazione rivenditori, reminder giornaliero e backup notturno.

Differenze rispetto alla specifica originale:

- la scheda linea ha **Prezzo** e **Modifica** e non **Disattiva**: una linea
  esce dal bot solo quando le righe spariscono dai pannelli (`prune-missing`),
  perché la cancellazione remota è la fonte di verità;
- etichetta, alias, note e dati tecnici si modificano dalla scheda con
  **Modifica**, un campo alla volta; il link VOD non è modificabile perché si
  genera dalle credenziali;
- il rinnovo non «resta in attesa di conferma pannello»: il sync successivo
  riallinea comunque la scadenza e l'eventuale differenza appare nel report;
- il job delle 09:00 manda il riepilogo all'owner (con le eccezioni da rivedere)
  e, per ogni rivenditore con `telegram_user_id` e `notify`, solo le sue linee;
  la deduplica è per chat, quindi owner e rivenditore possono ricevere la stessa
  linea senza doppioni.

## Owner

### Sync pannelli

Comandi owner, non wizard:

```
/sync            # legge i tre pannelli e riconcilia
/sync importa    # importa anche le righe non ancora collegate
```

Il messaggio riporta per piattaforma righe lette, nuove, collegate, importate,
scadenze aggiornate e quante righe restano "da rivedere". Nessuna scrittura
sui pannelli: il bot legge soltanto.

### Menu

```
/start
├── Scadenze            (oggi, 3 giorni, scadute)
├── Cerca cliente       (nome, alias, etichetta, codice, ID)
├── Nuovo cliente       (cliente + prima linea)
├── Incassi             (mese corrente per rivenditore + valore del parco)
├── Rivenditori
└── Impostazioni        (dominio VOD, modificabile)
```

### Ricerca

I risultati sono **linee**, non clienti.

```
🔎 «mario» — 3 risultati
• Mario Caterino · Linea 1 — scade il 13/11/2026 (tra 11 giorni) · 5,00 €
• Mario Caterino · Linea 2 — scade il 28/11/2026 (tra 26 giorni) · 6,00 €
• Mario Rossi · Linea unica — scaduta il 02/10/2026 (31 giorni fa) · 5,00 €

[👤 Mario Caterino · 2 linee]
```

Un tap apre la scheda della linea. Se il cliente ha più linee, la scheda
cliente le elenca tutte con il totale mensile.

### Scheda linea

```
🎬 Mario Caterino · Linea 1
alias: "Mario il rosso" · Diretto
Live + VOD · 5,00 €/mese
Live: mario2026 · VOD: mario-vod
scade il 13/11/2026 (tra 11 giorni)

[✅ Rinnova] [💶 Prezzo]
[✏️ Modifica]
[🚫 Non rinnovare]
[🕘 Storico] [👤 Cliente]
[🔎 Cerca]
[🏠 Menu]
```

**Modifica** apre l'elenco dei campi: Alias, Etichetta, Note e, se la linea ha
righe di pannello, **Dati pannello** (ID e codice per ciascun pannello, con il
valore attuale accanto). Si cambia un campo alla volta; `-` svuota il campo.
L'etichetta deve restare unica fra le linee attive dello stesso cliente: se è
già presa il bot lo dice e lascia lo stato in attesa, così si corregge senza
ripartire. Ogni modifica finisce in `audit_log`.

### Scheda cliente

**👤 Cliente** dalla scheda linea, oppure il pulsante che compare nei risultati
quando un cliente ha più linee:

```
👤 Mario Caterino
alias: "Mario il rosso"
3 linee attive · 18,00 €/mese

• Linea 1 — scade il 13/11/2026 (tra 11 giorni) · 5,00 €
• Linea 2 — scade il 01/12/2026 (tra 29 giorni) · 6,00 €
• Linea 3 — scade il 01/01/2027 · 7,00 €

[Linea 1 · 13/11] [Linea 2 · 01/12] [Linea 3 · 01/01]
```

### Rinnovo

Il rinnovo si registra dal bot; il conto sul pannello lo rinnova l'owner a mano.

```
[✅ Rinnova]  ->  [1 m] [2 m] [3 m]
                  [5 m] [12 m] [Altro…]
                  [⬅️ Annulla]
```

**Altro…** chiede i mesi come testo (1–36) e ha «Annulla» per tornare alla
scheda; aprire la scheda o il menu annulla l'attesa, quindi un numero digitato
dopo non rinnova più nulla.

Dopo il tap con i mesi:

1. calcola la nuova scadenza: dal giorno del rinnovo se la linea è scaduta,
   dalla scadenza corrente se è ancora attiva;
2. salva il rinnovo con prezzo e totale, e azzera l'eventuale marcatura
   «non rinnovare», con voce in `audit_log`;
3. risponde con la nuova data e il pulsante **↩️ Annulla**, valido 15 minuti;
4. l'annullamento riporta scadenza, giorno di ancoraggio e prezzo ai valori
   precedenti.

La linea non resta «in attesa di conferma»: la data del bot è quella che conta
finché un `/sync` non trova sul pannello una data diversa, e in quel caso vince
quella del pannello.

### Nuova linea (bot-first)

1. nome del cliente;
2. servizio (Live / Live+VOD);
3. alias (si può saltare);
4. rivenditore o diretto;
5. etichetta ("Linea 1", "Linea 2", saltata = "Linea unica");
6. prezzo mensile;
7. durata iniziale;
8. dati tecnici: ID pannello (si può saltare), codice di accesso (si può saltare);
9. riepilogo e conferma.

Alla fine il bot mostra il blocco da copiare nel pannello:

```
Da creare nel pannello LIVE
  username: mario2026
  password: Xy7k2p
  durata: 3 mesi
Da creare nel pannello VOD
  username: mario2026
  password: Xy7k2p
```

La linea resta `da creare nel pannello` finché il sync non la trova.

## Rivenditore (fase C)

- vede solo le linee con `reseller_id` uguale al suo;
- cerca, consulta la scheda, vede le scadenze delle sue linee;
- **non** modifica prezzi, scadenze o dati tecnici;
- può inviare una richiesta di rinnovo.

### Richiesta di rinnovo

```
[Richiedi rinnovo]
→ sceglie linea
→ sceglie mesi
→ nota opzionale
→ invia
```

All'owner arriva:

```
🔔 Richiesta di rinnovo
Mario (rivenditore) · Mario Caterino · Linea 1
Scade 13/11/2026 · richiesti 3 mesi
Prezzo proposto: 5,00 €/mese · 15,00 €

[1] [2] [3] [5] [12] [Altro] [Rifiuta]
```

L'owner può approvare con una durata diversa da quella richiesta. Alla
conferma il rinnovo viene registrato e la risposta torna al rivenditore e nel
gruppo di origine.

## Gruppo

Implementato: un gruppo può essere collegato a un rivenditore.

```
/collega            # il rivenditore collega il proprio gruppo
/collega Mario      # l'owner collega il gruppo indicando il nome
/scollega           # toglie il collegamento
```

Regole:

- nel gruppo il bot risponde solo a comandi indirizzati
  (`/scadenze@bot`, `/richiedi@bot`); `/start` e i testi liberi sono ignorati;
- chiunque nel gruppo collegato vede le scadenze e le ricerche di **quel**
  rivenditore, ma non può modificare nulla: prezzi, rinnovi e
  wizard restano in privato e richiedono l'identità personale (ID Telegram del
  rivenditore o dell'owner), non il gruppo;
- `/richiedi` in gruppo risponde «ti ho scritto in privato» e manda la scelta
  della linea in chat diretta; la richiesta nasce con `origin_chat_id` del
  gruppo, così l'approvazione viene confermata anche lì;
- un gruppo può essere collegato a un solo rivenditore: ricollegarlo lo sposta.

## Onboarding

Utente sconosciuto:

```
👋 Ciao! Questo bot è privato e non sei ancora autorizzato.
Il tuo Telegram ID è 123456789.
Comunicalo all'owner per farti abilitare.
```

All'owner:

```
🔐 Mario (ID 123456789) chiede accesso
[Autorizza come rivenditore] [Ignora]
```

## Reminder (fase D)

Ogni giorno alle 09:00 Europe/Rome:

- owner: riepilogo completo, raggruppato per rivenditore;
- rivenditore: solo le sue linee;
- nessun messaggio se non c'è nulla;
- deduplica per (tipo, linea, chat, giorno).
