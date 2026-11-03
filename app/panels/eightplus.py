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
    iso_to_date,
    to_int,
)
from app.panels.session import SessionStore

logger = logging.getLogger(__name__)

API_PREFIX = "/api"
PAGE_LIMIT = 100
MAX_PAGES = 100


class EightPlusAdapter:
    """Pannello 8Plus: JWT Bearer, refresh a un'ora, lista codici reseller."""

    platform = Platform.LIVE_8TVPLUS

    def __init__(
        self,
        *,
        base_url: str,
        email: str,
        password: str,
        tz: str,
        session_store: SessionStore | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )
        self._email = email
        self._password = password
        self._tz = tz
        self._store = session_store
        self._session_loaded = False
        self._access_token: str | None = None
        self._refresh_token: str | None = None

    async def fetch_lines(self) -> list[PanelLine]:
        await self._ensure_auth()

        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = await self._get(
                f"{API_PREFIX}/reseller/codes",
                params={"offset": offset, "limit": PAGE_LIMIT},
            )
            batch = payload.get("rows") or []
            if not isinstance(batch, list):
                raise PanelError("8Plus: risposta codes senza lista rows")
            rows.extend(row for row in batch if isinstance(row, dict))

            total = to_int(payload.get("total")) or 0
            if not batch or len(rows) >= total or len(rows) >= PAGE_LIMIT * MAX_PAGES:
                break
            offset += len(batch)

        lines: list[PanelLine] = []
        for row in rows:
            line = eightplus_row_to_panel_line(row, self._tz)
            if line is None:
                logger.warning("8Plus: riga senza id o codice, saltata")
                continue
            lines.append(line)
        return lines

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_download_domain(self) -> str | None:
        """8Plus non espone un dominio di download: il cliente usa il codice."""
        return None

    async def _ensure_auth(self) -> None:
        self._load_session()
        if self._access_token:
            return
        if self._refresh_token and await self._refresh():
            return
        await self._login()

    async def _login(self) -> None:
        response = await self._client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": self._email, "password": self._password},
        )
        if response.status_code >= 400:
            raise PanelAuthError(f"8Plus: login rifiutato ({response.status_code})")

        payload = _json_body(response, "POST", f"{API_PREFIX}/auth/login")
        if payload.get("totp_required"):
            raise PanelAuthError("8Plus: il pannello richiede il codice 2FA")
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise PanelAuthError("8Plus: login senza access_token")

        self._access_token = token
        refresh = payload.get("refresh_token")
        if isinstance(refresh, str) and refresh:
            self._refresh_token = refresh
        self._save_session()

    async def _refresh(self) -> bool:
        if not self._refresh_token:
            return False

        response = await self._client.post(
            f"{API_PREFIX}/auth/refresh",
            json={"refresh_token": self._refresh_token},
        )
        if response.status_code >= 400:
            self._access_token = None
            self._refresh_token = None
            self._save_session()
            return False

        payload = _json_body(response, "POST", f"{API_PREFIX}/auth/refresh")
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            return False

        self._access_token = token
        refresh = payload.get("refresh_token")
        if isinstance(refresh, str) and refresh:
            self._refresh_token = refresh
        self._save_session()
        return True

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        await self._ensure_auth()

        for attempt in range(2):
            headers = (
                {"Authorization": f"Bearer {self._access_token}"} if self._access_token else {}
            )
            response = await self._client.get(path, params=params, headers=headers)
            if response.status_code in (401, 403) and attempt == 0:
                if not await self._refresh():
                    await self._login()
                continue
            if response.status_code >= 400:
                raise PanelHTTPError("GET", path, response.status_code)
            return _json_body(response, "GET", path)

        raise PanelAuthError(f"8Plus: sessione non rinnovabile su {path}")

    def _load_session(self) -> None:
        if self._session_loaded:
            return
        self._session_loaded = True
        if self._store is None:
            return
        payload = self._store.load()
        access = payload.get("access_token")
        refresh = payload.get("refresh_token")
        self._access_token = access if isinstance(access, str) and access else None
        self._refresh_token = refresh if isinstance(refresh, str) and refresh else None

    def _save_session(self) -> None:
        if self._store is None:
            return
        self._store.save(
            {
                "access_token": self._access_token,
                "refresh_token": self._refresh_token,
                "saved_at": datetime.now(UTC).isoformat(),
            }
        )


def eightplus_row_to_panel_line(row: dict[str, Any], tz: str) -> PanelLine | None:
    code = str(row.get("code") or "").strip()
    external_id = str(row.get("id") or "").strip()
    if not code or not external_id:
        return None

    duration_days = to_int(row.get("duration_days"))
    note = row.get("note")
    return PanelLine(
        platform=Platform.LIVE_8TVPLUS,
        external_id=external_id,
        username=code,
        access_code=code,
        expires_on=iso_to_date(row.get("expires_at"), tz),
        status=_text(row.get("status")),
        note=_text(note),
        max_connections=to_int(row.get("max_connections")),
        package=f"{duration_days} giorni" if duration_days else None,
        raw=row,
    )


def _json_body(response: httpx.Response, method: str, path: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise PanelError(f"{method} {path}: risposta non JSON") from exc
    if not isinstance(payload, dict):
        raise PanelError(f"{method} {path}: risposta inattesa")
    return payload


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
