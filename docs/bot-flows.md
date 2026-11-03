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
[🔑 Codici]
[🚫 Non rinnovare]
[🕘 Storico] [👤 Cliente]
[🔎 Cerca]
[🏠 Menu]
```

**Modifica** apre l'elenco dei campi: Alias, Etichetta, Note, **Rivenditore** e,
se la linea ha righe di pannello, **Dati pannello** (ID e codice per ciascun pannello, con il
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
[🔑 Codici] [🏷 Rivenditore]
```

**🏷 Rivenditore** sposta **tutte le linee attive** del cliente su un rivenditore,
o le riporta a «Diretto»; il rivenditore vive sulla linea, quindi il `/sync` non
lo sovrascrive. Ogni linea cambiata lascia una voce in `audit_log`, più una voce
di riepilogo sul cliente. Per una singola linea si usa la stessa voce dentro
**Modifica** dalla scheda linea.

**🔑 Codici** mostra quello che il sync ha salvato per ogni pannello della linea:
username, password, codice di accesso, ID e scadenza del pannello. Per il VOD
c'è anche il **link di playlist** da consegnare al cliente:

```
<dominio>/get.php?username=<username>&password=<password>&type=m3u_plus&output=mpegts
```

Il dominio è l'impostazione `vod_domain` (menu Impostazioni); username e password
vengono dal sync. Se manca uno dei tre pezzi il link non si mostra e la vista
dice quale manca. I pannelli non espongono gli stessi campi: 8TV+ non ha password
(si accede con il codice, che coincide con lo username), il VOD non ha codice di
accesso. Il pulsante è solo per l'owner, e lo stesso link compare nel messaggio
finale di `/nuovo`.

### Rinnovo

Il rinnovo si registra dal bot; il conto sul pannello lo rinnova l'owner a mano.

```
[✅ Rinnova]  ->  [1] [2] [3] [4]
                  [5] [6] [7] [8]
                  [9] [10] [11] [12]
                  [Altro…]
                  [⬅️ Annulla]
```

Ogni mese da 1 a 12 ha il suo pulsante (griglia 4×3): si sceglie esattamente
quanti mesi servono, senza dover saltare i preset.

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

### Riepilogo nel gruppo del rivenditore

Un gruppo collegato con `/collega <nome>` riceve ogni giorno alle 09:00 (stessa
ora del reminder owner) un messaggio con le scadenze dei **prossimi 3 giorni**
(`reminder_days`), solo per le linee di quel rivenditore:

```
⏰ Scadenze · prossimi 3 giorni

• Mario Caterino · Linea 1 — scade domani (04/11/2026)
• Luca Bianchi · Linea unica — tra 2 giorni (05/11/2026)

Per chiedere un rinnovo: /richiedi
```

Niente pulsanti: in gruppo i comandi dell'owner non hanno senso. Il messaggio
parte ogni giorno, e a lista vuota dice «Nessuna scadenza nei prossimi 3
giorni». Un secondo giro dello stesso giorno non ripete le stesse linee (le
notifiche sono registrate per chat e giorno).
Serve che il gruppo sia collegato e che il rivenditore abbia `notify` attivo.

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

[1] … [12] [Altro] [Rifiuta]
```

La durata si sceglie con un pulsante per ogni mese da 1 a 12, oppure «Altro…»
per un valore a testo fino a 36. L'owner può approvare con una durata diversa da
quella richiesta. Alla
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

- nel gruppo il bot risponde a `/start` (menu a pulsanti), a `/cerca <nome>` e
  ai comandi indirizzati; il testo libero non è una ricerca, così la privacy
  mode può restare attiva;
- chiunque nel gruppo collegato vede le scadenze e le ricerche di **quel**
  rivenditore, ma non può modificare nulla: prezzi, rinnovi e
  wizard restano in privato e richiedono l'identità personale (ID Telegram del
  rivenditore o dell'owner), non il gruppo;
- `/richiedi` in gruppo risponde «ti ho scritto in privato» e manda la scelta
  della linea in chat diretta; la richiesta nasce con `origin_chat_id` del
  gruppo, così l'esito di approvazione o rifiuto viene annunciato anche lì;
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

### Anomalie del sync

Il `/sync` dice quante righe hanno qualcosa che non torna, ma non permette di
sistemarle. Le anomalie **con una linea** vengono salvate al termine del sync e
l'owner le gestisce una alla volta in privato.

```
/anomalie                 # elenco delle linee con un'anomalia aperta
/anomalia mario           # cerca la linea e apre la sua scheda
```

Nella scheda della linea compare `⚠️ Anomalia sync` quando c'è qualcosa da
sistemare, con le due date a confronto e due scelte:

- **🗓 Allinea alla data del pannello** — il pannello è la fonte di verità: la
  scadenza locale diventa quella del pannello e l'anomalia si chiude.
- **📌 Tieni la data locale** — il locale è più avanti perché il rinnovo sul
  pannello non è ancora stato fatto. L'anomalia viene accettata e non torna
  finché non scade la data locale: a quel punto, se il pannello non è stato
  rinnovato, ricompare.

Le anomalie senza linea (per esempio un username doppio nel pannello) restano
solo nel messaggio del sync: non c'è una linea su cui agire dal bot.

## Come si conta il guadagno

Due numeri diversi, e non vanno confusi:

- **Incassi del mese** — la somma dei rinnovi registrati nel mese, per
  rivenditore. È il denaro entrato.
- **Parco** — il giro mensile: somma di `monthly_price_cents` delle linee attive
  **e non scadute**, con clienti distinti e linee attive a confronto. Una linea
  con `expires_on` passato aspetta il rinnovo e non è guadagno: viene esclusa e
  contata a parte («Escluse N linee scadute: X €/mese in attesa di rinnovo»).
  Un cliente con Live e VOD ha una sola linea di incasso: il prezzo è del
  cliente, non del canale, quindi il totale non raddoppia.

Il messaggio di approvazione del rinnovo riporta l'importo (`mesi × prezzo
mensile`) sia all'owner sia al rivenditore, nel privato e nel gruppo.
