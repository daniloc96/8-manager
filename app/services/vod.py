"""Link di download consegnati al cliente.

Il link si compone dal dominio del pannello e dalle credenziali della riga:
non è modificabile a mano, perché è una funzione dei due valori.
"""

from __future__ import annotations

from urllib.parse import quote

# Parametri fissi richiesti dal pannello per la playlist.
PLAYLIST_PARAMS = "type=m3u_plus&output=mpegts"


def download_link(domain: str | None, username: str | None, password: str | None) -> str | None:
    """Link `get.php` completo, oppure None se manca un pezzo.

    Il dominio arriva dalle impostazioni, username e password dal sync: senza
    uno dei tre un link sarebbe rotto, quindi è meglio non mostrarlo.
    """
    if not domain or not username or not password:
        return None
    base = domain.rstrip("/")
    return (
        f"{base}/get.php?username={quote(username, safe='')}"
        f"&password={quote(password, safe='')}&{PLAYLIST_PARAMS}"
    )


def vod_link(domain: str | None, username: str | None, password: str | None) -> str | None:
    """Alias storico: il link VOD è un link di download come gli altri."""
    return download_link(domain, username, password)
