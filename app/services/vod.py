"""Link VOD consegnato al cliente.

Il link si compone dal dominio configurato e dalle credenziali della riga VOD:
non è modificabile a mano, perché è una funzione dei due valori.
"""

from __future__ import annotations

from urllib.parse import quote

# Parametri fissi richiesti dal pannello per la playlist.
PLAYLIST_PARAMS = "type=m3u_plus&output=mpegts"


def vod_link(domain: str | None, username: str | None, password: str | None) -> str | None:
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
