from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.models.enums import Platform
from app.panels.base import (
    PanelAuthError,
    PanelError,
    PanelHTTPError,
    PanelLine,
    to_int,
    unix_to_date,
)
from app.panels.session import SessionStore

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
PAGE_SIZE = 100
MAX_PAGES = 100


class LuckyAdapter:
    """LUCKY LIVE e VOD: stessa applicazione, base URL e credenziali diverse."""

    def __init__(
        self,
        *,
        platform: Platform,
        base_url: str,
        username: str,
        password: str,
        tz: str,
        session_store: SessionStore | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        if platform not in (Platform.LIVE_HALLO, Platform.VOD_NEWVOD):
            raise ValueError("LuckyAdapter gestisce solo LUCKY LIVE e VOD")
        self.platform = platform
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )
        self._username = username
        self._password = password
        self._tz = tz
        self._store = session_store
        self._session_loaded = False
        self._session_ready = False
        self._csrf: str | None = None

    async def fetch_lines(self) -> list[PanelLine]:
        await self._ensure_session()

        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = await self._get(
                f"{API_PREFIX}/lines",
                params={"page": page, "perPage": PAGE_SIZE},
            )
            if payload.get("ok") is False:
                raise PanelError(f"{self.platform.value}: lista linee rifiutata")

            data = payload.get("data")
            if isinstance(data, dict):
                batch = data.get("data") or []
                total = to_int(data.get("total")) or 0
            else:
                batch = payload.get("rows") or []
                total = to_int(payload.get("total")) or 0
            if not isinstance(batch, list):
                raise PanelError(f"{self.platform.value}: risposta linee senza lista")

            rows.extend(row for row in batch if isinstance(row, dict))
            if not batch or len(rows) >= total or page >= MAX_PAGES:
                break
            page += 1

        lines: list[PanelLine] = []
        for row in rows:
            line = lucky_row_to_panel_line(row, self.platform, self._tz)
            if line is None:
                logger.warning("%s: riga senza id o username, saltata", self.platform.value)
                continue
            lines.append(line)
        return lines

    async def packages(self, line_id: int) -> list[dict[str, Any]]:
        """Pacchetti di rinnovo disponibili per una linea (serve alla fase F)."""
        await self._ensure_session()
        payload = await self._get(f"{API_PREFIX}/lines/editor", params={"id": line_id})
        data = payload.get("data")
        container = data if isinstance(data, dict) else payload
        packages = container.get("packages") or []
        return [item for item in packages if isinstance(item, dict)]

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _ensure_session(self) -> None:
        self._load_session()
        if self._session_ready:
            return

        if self._csrf and len(self._client.cookies) > 0:
            response = await self._client.get(f"{API_PREFIX}/session")
            if response.status_code == 200:
                payload = _safe_json(response)
                self._csrf = _extract_csrf(payload) or self._csrf
                self._session_ready = True
                self._save_session()
                return
            if response.status_code not in (401, 403):
                raise PanelHTTPError("GET", f"{API_PREFIX}/session", response.status_code)

        await self._login()

    async def _login(self) -> None:
        # Una sessione scaduta lascia cookie che il pannello rifiuta al login.
        # Riparti sempre da un client pulito, altrimenti il login resta bloccato
        # finché qualcuno non cancella a mano il file di sessione.
        self._client.cookies.clear()
        self._csrf = None
        self._session_ready = False
        if self._store is not None:
            self._store.clear()

        seed = await self._client.get(f"{API_PREFIX}/auth/login")
        if seed.status_code >= 400:
            raise PanelHTTPError("GET", f"{API_PREFIX}/auth/login", seed.status_code)
        csrf = _extract_csrf(_safe_json(seed))

        headers = {"X-CSRF-Token": csrf} if csrf else {}
        response = await self._client.post(
            f"{API_PREFIX}/auth/login",
            json={
                "username": self._username,
                "password": self._password,
                "referrer": "/",
            },
            headers=headers,
        )
        if response.status_code in (401, 403):
            raise PanelAuthError(f"{self.platform.value}: login rifiutato")
        if response.status_code >= 400:
            raise PanelHTTPError("POST", f"{API_PREFIX}/auth/login", response.status_code)

        payload = _safe_json(response)
        if payload is not None and payload.get("ok") is False:
            raise PanelAuthError(f"{self.platform.value}: login rifiutato")
        csrf = _extract_csrf(payload) or csrf

        session = await self._client.get(f"{API_PREFIX}/session")
        if session.status_code in (401, 403):
            raise PanelAuthError(f"{self.platform.value}: sessione non creata dopo il login")
        if session.status_code >= 400:
            raise PanelHTTPError("GET", f"{API_PREFIX}/session", session.status_code)

        session_payload = _safe_json(session)
        self._csrf = _extract_csrf(session_payload) or csrf
        if not self._csrf:
            raise PanelAuthError(f"{self.platform.value}: CSRF non trovato dopo il login")

        self._session_ready = True
        self._save_session()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        await self._ensure_session()

        for attempt in range(2):
            headers = {"X-CSRF-Token": self._csrf} if self._csrf else {}
            response = await self._client.get(path, params=params, headers=headers)
            if response.status_code in (401, 403) and attempt == 0:
                await self._login()
                continue
            if response.status_code >= 400:
                raise PanelHTTPError("GET", path, response.status_code)
            payload = _safe_json(response)
            if payload is None:
                raise PanelError(f"GET {path}: risposta non JSON")
            return payload

        raise PanelAuthError(f"{self.platform.value}: sessione non rinnovabile su {path}")

    def _load_session(self) -> None:
        if self._session_loaded:
            return
        self._session_loaded = True
        if self._store is None:
            return
        payload = self._store.load()
        cookies = payload.get("cookies")
        if isinstance(cookies, dict):
            self._client.cookies.update({str(name): str(value) for name, value in cookies.items()})
        csrf = payload.get("csrf")
        self._csrf = csrf if isinstance(csrf, str) and csrf else None

    def _save_session(self) -> None:
        if self._store is None:
            return
        cookies = {cookie.name: cookie.value for cookie in self._client.cookies.jar}
        self._store.save(
            {
                "cookies": cookies,
                "csrf": self._csrf,
                "saved_at": datetime.now(UTC).isoformat(),
            }
        )


def lucky_row_to_panel_line(row: dict[str, Any], platform: Platform, tz: str) -> PanelLine | None:
    username = str(row.get("username") or "").strip()
    external_id = str(row.get("id") or "").strip()
    if not username or not external_id:
        return None

    note = row.get("notes")
    if note is None:
        note = row.get("note")
    return PanelLine(
        platform=platform,
        external_id=external_id,
        username=username,
        password=_text(row.get("password")),
        access_code=_text(row.get("access_code")),
        expires_on=unix_to_date(row.get("exp_date"), tz),
        status=_text(row.get("status")),
        note=_text(note),
        max_connections=to_int(row.get("max_connections")),
        raw=row,
    )


def _safe_json(response: httpx.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_csrf(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    for key in ("csrf", "csrf_token"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    for key in ("data", "meta"):
        value = payload.get(key)
        if isinstance(value, dict):
            found = _extract_csrf(value)
            if found:
                return found
    return None


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
