from __future__ import annotations

from datetime import UTC, datetime

from app.models import Platform, SyncStatus
from app.panels.report import report_html, summary_lines
from app.panels.sync import PlatformResult, ReviewItem, SyncReport


def _report() -> SyncReport:
    return SyncReport(
        started_at=datetime(2026, 11, 2, 10, 0, tzinfo=UTC),
        finished_at=datetime(2026, 11, 2, 10, 1, tzinfo=UTC),
        results=[
            PlatformResult(
                platform=Platform.LIVE_HALLO,
                fetched=2,
                imported=1,
                review=[
                    ReviewItem(
                        platform=Platform.VOD_NEWVOD,
                        username="<mario>",
                        reason="nota & nome diversi",
                    )
                ],
            ),
            PlatformResult(
                platform=Platform.LIVE_8TVPLUS,
                status=SyncStatus.FAILED,
                error="timeout",
            ),
        ],
        skipped=[(Platform.VOD_NEWVOD, "credenziali mancanti")],
    )


def test_summary_lines_include_counts_and_reviews() -> None:
    lines = summary_lines(_report())
    text = "\n".join(lines)
    assert "LUCKY LIVE" in text
    assert "1 importate" in text
    assert "8Plus: " in text
    assert "timeout" in text
    assert "credenziali mancanti" in text
    assert "<mario>" in text


def test_report_html_escapes_dynamic_text() -> None:
    body = report_html(_report())
    assert "&lt;mario&gt;" in body
    assert "nota &amp; nome diversi" in body
    assert "❌" in body
    assert "⏭️" in body
