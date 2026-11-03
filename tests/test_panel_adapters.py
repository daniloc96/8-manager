from __future__ import annotations

import stat
from datetime import UTC, date, datetime

import httpx

from app.models import Platform
from app.panels.eightplus import EightPlusAdapter
from app.panels.lucky import LuckyAdapter
from app.panels.session import SessionStore

EIGHT_PLUS_ROWS = [
    {
        "id": "uuid-1",
        "code": "CODE1",
        "note": "Mario Rossi",
        "status": "active",
        "expires_at": "2026-11-12T16:30:20+00:00",
        "duration_days": 30,
        "max_connections": 1,
    },
    {
        "id": "uuid-2",
        "code": "CODE2",
        "note": "Anna Bianchi",
        "status": "expired",
        "expires_at": "2026-10-01T08:00:00+00:00",
        "duration_days": 90,
        "max_connections": 2,
    },
]

LUCKY_ROWS = [
    {
        "id": 512148,
        "username": "paoloc111026",
        "password": "segreta",
        "access_code": "KSUQ678257",
        "exp_date": int(datetime(2026, 11, 15, 9, 0, tzinfo=UTC).timestamp()),
        "status": "active",
        "notes": "Paolo Consiglio",
        "max_connections": 1,
    }
]


async def test_eightplus_paginates_and_parses_rows(tmp_path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/auth/login":
            return httpx.Response(
                200,
                json={"access_token": "access-1", "refresh_token": "refresh-1"},
            )
        if request.url.path == "/api/reseller/codes":
            assert request.headers["authorization"] == "Bearer access-1"
            offset = int(request.url.params.get("offset", "0"))
            if offset == 0:
                return httpx.Response(200, json={"rows": [EIGHT_PLUS_ROWS[0]], "total": 2})
            return httpx.Response(200, json={"rows": [EIGHT_PLUS_ROWS[1]], "total": 2})
        return httpx.Response(404)

    store = SessionStore(tmp_path / "sessions" / "eightplus.json")
    adapter = EightPlusAdapter(
        base_url="https://panel.test",
        email="owner@example.test",
        password="password",
        tz="Europe/Rome",
        session_store=store,
        transport=httpx.MockTransport(handler),
    )
    try:
        rows = await adapter.fetch_lines()
    finally:
        await adapter.aclose()

    assert len(rows) == 2
    assert rows[0].platform is Platform.LIVE_8TVPLUS
    assert rows[0].username == "CODE1"
    assert rows[0].access_code == "CODE1"
    assert rows[0].expires_on == date(2026, 11, 12)
    assert rows[0].note == "Mario Rossi"
    assert rows[0].package == "30 giorni"
    assert rows[1].max_connections == 2
    assert len([request for request in requests if request.url.path == "/api/reseller/codes"]) == 2

    assert store.path.exists()
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


async def test_eightplus_refreshes_on_unauthorized(tmp_path) -> None:
    codes_auth: list[str] = []
    refreshed = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refreshed
        if request.url.path == "/api/auth/login":
            return httpx.Response(
                200, json={"access_token": "access-1", "refresh_token": "refresh-1"}
            )
        if request.url.path == "/api/auth/refresh":
            refreshed = True
            return httpx.Response(200, json={"access_token": "access-2"})
        if request.url.path == "/api/reseller/codes":
            authorization = request.headers["authorization"]
            codes_auth.append(authorization)
            if authorization == "Bearer access-1":
                return httpx.Response(401, json={"error": "expired"})
            return httpx.Response(200, json={"rows": [EIGHT_PLUS_ROWS[0]], "total": 1})
        return httpx.Response(404)

    adapter = EightPlusAdapter(
        base_url="https://panel.test",
        email="owner@example.test",
        password="password",
        tz="Europe/Rome",
        transport=httpx.MockTransport(handler),
    )
    try:
        rows = await adapter.fetch_lines()
    finally:
        await adapter.aclose()

    assert refreshed is True
    assert codes_auth == ["Bearer access-1", "Bearer access-2"]
    assert rows[0].username == "CODE1"


async def test_lucky_login_discards_expired_session(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions" / "lucky.json")
    store.save({"cookies": {"session": "stale"}, "csrf": "old-csrf"})
    login_cookies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cookie = request.headers.get("cookie", "")
        if request.url.path == "/api/v1/session" and "session=stale" in cookie:
            return httpx.Response(401, json={"ok": False})
        if request.url.path == "/api/v1/auth/login" and request.method == "GET":
            return httpx.Response(200, json={"csrf": "seed-csrf"})
        if request.url.path == "/api/v1/auth/login" and request.method == "POST":
            login_cookies.append(cookie)
            if "session=stale" in cookie:
                return httpx.Response(401, json={"ok": False})
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/api/v1/session":
            return httpx.Response(200, json={"csrf": "live-csrf"})
        if request.url.path == "/api/v1/lines":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "data": {"data": LUCKY_ROWS, "total": 1, "page": 1, "perPage": 100},
                },
            )
        return httpx.Response(404)

    adapter = LuckyAdapter(
        platform=Platform.LIVE_HALLO,
        base_url="http://panel.test",
        username="owner@example.test",
        password="password",
        tz="Europe/Rome",
        session_store=store,
        transport=httpx.MockTransport(handler),
    )
    try:
        rows = await adapter.fetch_lines()
    finally:
        await adapter.aclose()

    assert len(rows) == 1
    assert login_cookies
    assert all("session=stale" not in cookie for cookie in login_cookies)


async def test_lucky_login_csrf_and_parsing() -> None:
    login_headers: list[str | None] = []
    line_headers: list[str | None] = []
    cookie_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login" and request.method == "GET":
            return httpx.Response(
                200,
                json={"csrf": "seed-csrf"},
                headers={"set-cookie": "session=abc; Path=/"},
            )
        if request.url.path == "/api/v1/auth/login" and request.method == "POST":
            login_headers.append(request.headers.get("x-csrf-token"))
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/api/v1/session":
            return httpx.Response(200, json={"user": {"id": 1}, "csrf": "live-csrf"})
        if request.url.path == "/api/v1/lines":
            line_headers.append(request.headers.get("x-csrf-token"))
            cookie_headers.append(request.headers.get("cookie"))
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "data": {"data": LUCKY_ROWS, "total": 1, "page": 1, "perPage": 100},
                },
            )
        if request.url.path == "/api/v1/lines/editor":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "data": {"packages": [{"id": 42, "name": "3 MESI (MONTH) - (All Package)"}]},
                },
            )
        return httpx.Response(404)

    adapter = LuckyAdapter(
        platform=Platform.LIVE_HALLO,
        base_url="http://panel.test",
        username="owner@example.test",
        password="password",
        tz="Europe/Rome",
        transport=httpx.MockTransport(handler),
    )
    try:
        rows = await adapter.fetch_lines()
        packages = await adapter.packages(512148)
    finally:
        await adapter.aclose()

    assert login_headers == ["seed-csrf"]
    assert line_headers == ["live-csrf"]
    assert cookie_headers and cookie_headers[0] and "session=abc" in cookie_headers[0]
    assert len(rows) == 1
    assert rows[0].platform is Platform.LIVE_HALLO
    assert rows[0].username == "paoloc111026"
    assert rows[0].password == "segreta"
    assert rows[0].access_code == "KSUQ678257"
    assert rows[0].expires_on == date(2026, 11, 15)
    assert rows[0].note == "Paolo Consiglio"
    assert packages[0]["id"] == 42


async def test_lucky_reads_download_domain_from_meta() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login" and request.method == "GET":
            return httpx.Response(200, json={"csrf": "seed-csrf"})
        if request.url.path == "/api/v1/auth/login" and request.method == "POST":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/api/v1/session":
            return httpx.Response(200, json={"csrf": "live-csrf"})
        if request.url.path == "/api/v1/lines/meta":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "data": {"download": {"base_url": "http://hulu.test:23000/"}},
                },
            )
        return httpx.Response(404)

    adapter = LuckyAdapter(
        platform=Platform.LIVE_HALLO,
        base_url="http://panel.test",
        username="owner@example.test",
        password="password",
        tz="Europe/Rome",
        transport=httpx.MockTransport(handler),
    )
    try:
        domain = await adapter.fetch_download_domain()
    finally:
        await adapter.aclose()

    assert domain == "http://hulu.test:23000"


async def test_lucky_meta_without_download_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login" and request.method == "GET":
            return httpx.Response(200, json={"csrf": "seed-csrf"})
        if request.url.path == "/api/v1/auth/login" and request.method == "POST":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/api/v1/session":
            return httpx.Response(200, json={"csrf": "live-csrf"})
        if request.url.path == "/api/v1/lines/meta":
            return httpx.Response(200, json={"ok": True, "data": {"download": {}}})
        return httpx.Response(404)

    adapter = LuckyAdapter(
        platform=Platform.VOD_NEWVOD,
        base_url="http://panel.test",
        username="owner@example.test",
        password="password",
        tz="Europe/Rome",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert await adapter.fetch_download_domain() is None
    finally:
        await adapter.aclose()


async def test_eightplus_has_no_download_domain() -> None:
    adapter = EightPlusAdapter(
        base_url="https://panel.test",
        email="owner@example.test",
        password="password",
        tz="Europe/Rome",
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )
    try:
        assert await adapter.fetch_download_domain() is None
    finally:
        await adapter.aclose()
