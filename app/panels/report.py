from __future__ import annotations

import html
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.models import Platform, SyncStatus
from app.panels.sync import PlatformResult, SyncReport

PLATFORM_LABELS = {
    Platform.LIVE_8TVPLUS: "8Plus",
    Platform.LIVE_HALLO: "LUCKY LIVE",
    Platform.VOD_NEWVOD: "VOD",
}


def platform_label(platform: Platform) -> str:
    return PLATFORM_LABELS.get(platform, platform.value)


def summary_lines(report: SyncReport, *, review_limit: int = 50) -> list[str]:
    local = report.started_at.astimezone(ZoneInfo(get_settings().tz))
    title = f"Sync pannelli · {local:%d/%m/%Y %H:%M}"
    if report.dry_run:
        title += " · dry-run"
    if report.import_missing:
        title += " · import"

    lines = [title]
    for result in report.results:
        lines.append(_result_line(result))
    for platform, reason in report.skipped:
        lines.append(f"⏭️ {platform_label(platform)}: {reason}")

    reviews = _review_lines(report, limit=review_limit)
    if reviews:
        lines.append("")
        lines.append("Da rivedere:")
        lines.extend(reviews)
    if report.suppressed:
        lines.append("")
        lines.append(f"In eccezione (nascoste): {report.suppressed}")
    for line_id, reason, review_on in report.due_exceptions:
        lines.append(
            f"⏰ linea {line_id} · eccezione da rivedere dal {_italian_date(review_on)} — {reason}"
        )
    return lines


def report_html(report: SyncReport, *, review_limit: int = 8) -> str:
    local = report.started_at.astimezone(ZoneInfo(get_settings().tz))
    lines = [f"🔄 <b>Sync pannelli</b> · {local:%d/%m/%Y %H:%M}"]
    if report.dry_run:
        lines.append("<i>dry-run: nessuna scrittura</i>")
    if report.import_missing:
        lines.append("<i>con import delle linee mancanti</i>")

    for result in report.results:
        lines.append(html.escape(_result_line(result)))
    for platform, reason in report.skipped:
        lines.append(f"⏭️ {html.escape(platform_label(platform))}: {html.escape(reason)}")

    reviews = [review for result in report.results for review in result.review]
    if reviews:
        lines.append("")
        lines.append(f"⚠️ <b>Da rivedere</b> ({len(reviews)})")
        for review in reviews[:review_limit]:
            lines.append(
                f"• <code>{html.escape(review.username)}</code> — {html.escape(review.reason)}"
            )
        remaining = len(reviews) - review_limit
        if remaining > 0:
            lines.append(f"… e altre {remaining} righe (dettaglio con <code>sync</code>)")
    elif not report.failed:
        lines.append("")
        lines.append("✅ Nessuna riga da rivedere.")

    if report.suppressed:
        lines.append(f"🔕 <b>In eccezione</b>: {report.suppressed} anomalie accettate")
    for line_id, reason, review_on in report.due_exceptions:
        lines.append(
            f"⏰ linea {line_id} · eccezione da rivedere dal "
            f"{_italian_date(review_on)} — {html.escape(reason)}"
        )

    return "\n".join(lines)


def _italian_date(value: str) -> str:
    year, month, day = value.split("-")
    return f"{day}/{month}/{year}"


def _result_line(result: PlatformResult) -> str:
    label = platform_label(result.platform)
    if result.status is SyncStatus.FAILED:
        return f"❌ {label}: {result.error or 'errore sconosciuto'}"

    parts = [f"{result.fetched} righe"]
    if result.created:
        parts.append(f"{result.created} nuove")
    if result.updated:
        parts.append(f"{result.updated} aggiornate")
    if result.linked:
        parts.append(f"{result.linked} collegate")
    if result.imported:
        parts.append(f"{result.imported} importate")
    if result.expiry_updates:
        parts.append(f"{result.expiry_updates} scadenze aggiornate")
    if result.renewals:
        parts.append(f"{result.renewals} rinnovi registrati")
    if result.historical:
        parts.append(f"{result.historical} storiche")
    if result.missing:
        parts.append(f"{result.missing} sparite")
    if result.review:
        parts.append(f"{len(result.review)} da rivedere")
    return f"{label}: " + " · ".join(parts)


def _review_lines(report: SyncReport, *, limit: int) -> list[str]:
    lines: list[str] = []
    for result in report.results:
        for review in result.review:
            if len(lines) >= limit:
                remaining = report.review_count - len(lines)
                lines.append(f"  … e altre {remaining} righe")
                return lines
            lines.append(
                f"  {platform_label(review.platform)} · {review.username} — {review.reason}"
            )
    return lines
