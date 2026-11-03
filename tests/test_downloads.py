from __future__ import annotations

from datetime import date

from app.models import Customer, Line, PanelLine, Platform, Service
from app.services.downloads import line_downloads, load_domains, save_domain
from app.services.text import customer_search_key
from app.services.vod import download_link


def make_line(*panel_lines: PanelLine) -> Line:
    line = Line(
        customer=Customer(name="Mario Rossi", search_key=customer_search_key("Mario Rossi")),
        service=Service.LIVE_VOD,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=date(2026, 12, 1),
        anchor_day=1,
    )
    line.panel_lines = list(panel_lines)
    return line


def panel(platform: Platform, username: str, password: str | None) -> PanelLine:
    return PanelLine(
        platform=platform,
        username=username,
        username_key=username.lower(),
        password=password,
    )


def test_download_link_matches_panel_format() -> None:
    link = download_link("http://listavod.example:24000/", "mario2026", "p&ss word")

    assert link is not None
    assert link.startswith("http://listavod.example:24000/get.php?")
    assert "username=mario2026" in link
    assert "password=p%26ss%20word" in link
    assert "type=m3u_plus" in link
    assert "output=mpegts" in link


def test_download_link_requires_credentials_and_domain() -> None:
    assert download_link(None, "mario", "password") is None
    assert download_link("http://vod.test", "mario", None) is None
    assert download_link("http://vod.test", "", "password") is None


def test_line_downloads_uses_each_panel_password() -> None:
    line = make_line(
        panel(Platform.LIVE_HALLO, "mario-live", "live-pass"),
        panel(Platform.VOD_NEWVOD, "mario-vod", "vod-pass"),
    )

    links = line_downloads(
        line,
        {
            Platform.LIVE_HALLO: "http://hulu.test:23000",
            Platform.VOD_NEWVOD: "http://vod.test:24000",
        },
    )

    assert [label for label, _url in links] == ["Live", "VOD"]
    assert "password=live-pass" in links[0][1]
    assert "password=vod-pass" in links[1][1]


def test_line_downloads_skips_8plus_and_missing_panels() -> None:
    eight_plus = make_line(panel(Platform.LIVE_8TVPLUS, "CODE1", None))
    assert line_downloads(eight_plus, {Platform.LIVE_HALLO: "http://hulu.test"}) == []

    live_without_password = make_line(panel(Platform.LIVE_HALLO, "mario", None))
    assert line_downloads(live_without_password, {Platform.LIVE_HALLO: "http://hulu.test"}) == []


async def test_save_domain_normalizes_and_is_idempotent(session) -> None:
    assert await save_domain(session, Platform.LIVE_HALLO, "http://hulu.test:23000/") is True
    assert await save_domain(session, Platform.LIVE_HALLO, "http://hulu.test:23000") is False
    await session.flush()

    domains = await load_domains(session)
    assert domains[Platform.LIVE_HALLO] == "http://hulu.test:23000"


async def test_save_domain_dry_run_does_not_write(session) -> None:
    changed = await save_domain(session, Platform.VOD_NEWVOD, "http://vod.test", apply=False)

    assert changed is True
    assert await load_domains(session) == {}
