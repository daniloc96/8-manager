from __future__ import annotations

START_OWNER = """👋 <b>Ciao!</b>

Sono il bot di gestione abbonamenti.

<b>Comandi</b>
/start · /scadenze · /cerca · /incassi · /stato · /sync · /help
"""

START_GUEST = """👋 Ciao {name}!

Questo bot è privato e non sei ancora autorizzato.

Il tuo Telegram ID è <code>{user_id}</code>.
Comunicalo all'owner per farti abilitare.
"""

MENU = """🏠 <b>Menu</b>

📅 <b>Scadenze</b> — oggi, prossimi giorni e scadute
🔎 <b>Cerca</b> — nome, alias, etichetta o username
💰 <b>Incassi</b> — rinnovi del mese per rivenditore
👥 <b>Rivenditori</b> — linee e prezzi per rivenditore
⚙️ <b>Impostazioni</b> — valori e conteggi
"""


RESELLER_MENU = """🏠 <b>Menu</b> · {name}

📅 <b>Scadenze</b> — le tue linee in scadenza
🔎 <b>Cerca</b> — fra le tue linee
🔁 <b>Richiedi rinnovo</b> — l'owner conferma

<i>Prezzi e scadenze si modificano solo dall'owner.</i>
"""


def due_summary(total: int) -> str:
    if total == 0:
        return "✅ Nessuna linea scaduta o in scadenza."
    if total == 1:
        return "📅 <b>1 linea</b> scaduta o in scadenza. Apri <b>Scadenze</b>."
    return f"📅 <b>{total} linee</b> scadute o in scadenza. Apri <b>Scadenze</b>."


HELP_OWNER = """<b>Comandi</b>

/start — menu principale
/scadenze — scadute, oggi e prossimi giorni
/cerca &lt;testo&gt; — cerca una linea
/incassi — rinnovi del mese
/stato — stato del bot, database e heartbeat
/richiedi — richiesta di rinnovo (per i rivenditori)
/sync — sincronizza le linee dai pannelli
/sync importa — importa anche le linee non collegate
/id — il tuo ID Telegram e quello della chat
/help — questo messaggio

<i>Scrivere un testo senza comando equivale a cercarlo.</i>
"""

HELP_RESELLER = """<b>Comandi</b>

/start — menu
/scadenze — le tue linee in scadenza
/cerca &lt;testo&gt; — cerca fra le tue linee
/richiedi — richiedi un rinnovo all'owner
/id — il tuo ID Telegram
/help — questo messaggio

<i>Prezzi e scadenze si modificano solo dall'owner.</i>
"""

HELP_GUEST = """Questo bot è privato.

Scrivi /id per ottenere il tuo Telegram ID e comunicarlo all'owner.
"""

UNAUTHORIZED = """⛔ Non sei autorizzato.

Il tuo Telegram ID è <code>{user_id}</code>.
"""

SEARCH_PROMPT = """🔎 Scrivi parte del nome, dell'alias, dell'etichetta o dello
username del pannello.

Puoi anche usare <code>/cerca Mario</code>."""

SEARCH_EMPTY = """🔎 Nessuna linea trovata per «{query}».

Prova con una parte del nome del cliente, un alias o lo username del pannello."""

SEARCH_TITLE = """🔎 «{query}» — {count} risultati"""

SYNC_RUNNING = """⏳ <b>Sync pannelli…</b>

Leggo 8Plus, LUCKY LIVE e VOD. Non modifico i pannelli.
"""

SYNC_RUNNING_IMPORT = """⏳ <b>Sync pannelli…</b>

Leggo i pannelli e importo le linee non ancora collegate.
"""

SYNC_NO_PANELS = """⚙️ Nessun pannello configurato.

Compila <code>PANEL_*</code> in <code>.env</code> e riprova.
"""

SYNC_PRIVATE_ONLY = """🔒 Il sync si lancia in privato.

Il report contiene username e dati delle linee: non va in un gruppo.
"""

SYNC_ERROR = """❌ Sync non avviato: <code>{error}</code>"""


STATUS = """<b>Stato del bot</b>

Versione: <code>{version}</code>
Database: <code>{database}</code>
Clienti: <b>{customers}</b> · Linee: <b>{lines}</b> · Rinnovi: <b>{renewals}</b>
Heartbeat: <b>{heartbeat}</b>
"""
