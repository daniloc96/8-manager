from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app import __version__
from app.config import get_settings
from app.db import dispose_engine


def _run(coro):
    async def wrapper():
        try:
            return await coro
        finally:
            await dispose_engine()

    return asyncio.run(wrapper())


def cmd_migrate(_args: argparse.Namespace) -> int:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")
    return 0


def cmd_backup(_args: argparse.Namespace) -> int:
    from app.services.backup import BackupError, backup_database, sqlite_path

    settings = get_settings()
    try:
        target = backup_database(
            sqlite_path(settings.database_url),
            settings.backup_dir,
            settings.backup_retention_days,
        )
    except BackupError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(target)
    return 0


async def _counts() -> tuple[int, int, int, int]:
    from app.db import session_scope
    from app.models import Customer, Line, Renewal

    async with session_scope() as session:
        customers = await session.scalar(select(func.count(Customer.id))) or 0
        lines = await session.scalar(select(func.count(Line.id)).where(Line.deleted_at.is_(None)))
        lines_total = await session.scalar(select(func.count(Line.id))) or 0
        renewals = await session.scalar(select(func.count(Renewal.id))) or 0
    return customers, lines or 0, lines_total, renewals


def cmd_check(_args: argparse.Namespace) -> int:
    settings = get_settings()
    print(f"versione: {__version__}")
    print(f"database: {settings.database_url}")
    print(f"data dir: {settings.data_dir}")
    print(f"token: {'ok' if settings.bot_token else 'mancante'}")
    print(f"owner: {settings.owner_telegram_id or 'mancante'}")
    try:
        customers, lines, lines_total, renewals = _run(_counts())
    except Exception as exc:
        print(f"database: errore ({exc})")
        return 1
    print(
        f"clienti: {customers} · linee attive: {lines} · totali: {lines_total} "
        f"· rinnovi: {renewals}"
    )
    return 0


async def _seed_demo() -> None:
    from datetime import timedelta

    from app.db import session_scope
    from app.models import Service
    from app.models.enums import RenewalSource
    from app.services.lines import LineDraft, create_customer_and_line, renew_line

    settings = get_settings()
    today = datetime.now(ZoneInfo(settings.tz)).date()

    async with session_scope() as session:
        _customer, first = await create_customer_and_line(
            session,
            LineDraft(
                name="Mario Rossi",
                alias="Mario il rosso",
                service=Service.LIVE_VOD,
                monthly_price_cents=500,
                months=1,
                label="Linea 1",
                start_on=today - timedelta(days=40),
            ),
            today,
        )
        await renew_line(
            session,
            first,
            months=2,
            today=today,
            source=RenewalSource.MANUAL,
            note="rinnovo demo",
        )
        _customer_two, second = await create_customer_and_line(
            session,
            LineDraft(
                name="Anna Bianchi",
                service=Service.LIVE,
                monthly_price_cents=400,
                months=3,
                start_on=today,
            ),
            today,
        )
    print(f"linee demo create: {first.id}, {second.id}")


def cmd_seed_demo(_args: argparse.Namespace) -> int:
    _run(_seed_demo())
    return 0


async def _sync_panels(args: argparse.Namespace) -> int:
    from app.panels.factory import build_adapters
    from app.panels.report import summary_lines
    from app.panels.sync import run_sync

    adapters, skipped = build_adapters(
        platforms=[args.platform],
        snapshot_dir=args.snapshot,
    )
    if not adapters:
        print("Nessun pannello configurato: compila PANEL_* in .env o usa --snapshot.")
        for platform, reason in skipped:
            print(f"  {platform.value}: {reason}")
        return 1

    report = await run_sync(
        adapters,
        import_missing=args.import_missing,
        dry_run=args.dry_run,
        skipped=skipped,
    )
    print("\n".join(summary_lines(report)))
    return 1 if report.failed else 0


def cmd_sync(args: argparse.Namespace) -> int:
    return _run(_sync_panels(args))


async def _link_panel(args: argparse.Namespace) -> int:
    from app.db import session_scope
    from app.panels.factory import PLATFORM_ALIASES
    from app.services.panel_links import PanelLinkError, link_panel_line

    platform = PLATFORM_ALIASES.get(args.platform)
    if platform is None:
        print(f"pannello sconosciuto: {args.platform}", file=sys.stderr)
        return 1

    try:
        async with session_scope() as session:
            line, panel_line = await link_panel_line(
                session,
                platform=platform,
                username=args.username,
                line_id=args.line,
                unlink=args.unlink,
            )
            action = "scollegata" if args.unlink else "collegata"
            print(
                f"{panel_line.platform.value}/{panel_line.username} {action} "
                f"alla linea {line.id} ({line.customer.name}, "
                f"{line.service.value}, scade {line.expires_on})"
            )
    except PanelLinkError as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_link_panel(args: argparse.Namespace) -> int:
    return _run(_link_panel(args))


def _today():
    return datetime.now(ZoneInfo(get_settings().tz)).date()


async def _prune_missing(args: argparse.Namespace) -> int:
    from app.db import session_scope
    from app.panels.report import platform_label
    from app.services.prune import PruneBlocked, prune_missing

    try:
        async with session_scope() as session:
            report = await prune_missing(
                session,
                today=_today(),
                apply=args.apply,
                force=args.force,
            )
    except PruneBlocked as exc:
        print("prune bloccato: troppe righe sparite", file=sys.stderr)
        for platform, ratio in sorted(exc.ratios.items()):
            print(f"  {platform_label(platform)}: {ratio:.0%} delle righe", file=sys.stderr)
        print("controlla i pannelli e riprova con --force", file=sys.stderr)
        return 1

    for platform in sorted(report.missing_by_platform, key=lambda item: item.value):
        missing = report.missing_by_platform[platform]
        total = report.total_by_platform.get(platform, 0)
        print(f"{platform_label(platform)}: {missing} sparite su {total}")

    print(f"righe orfane da cancellare: {report.orphan_rows}")
    if report.to_close:
        print(f"\nlinee da chiudere ({len(report.to_close)}):")
        for line_id, customer, expires_on in report.to_close:
            print(f"  {line_id} · {customer} · scaduta il {expires_on}")
    if report.undecided:
        print(f"\nda decidere ({len(report.undecided)}):")
        for line_id, customer, expires_on, usernames, why in report.undecided:
            print(f"  {line_id} · {customer} · scade {expires_on} · {why} ({', '.join(usernames)})")

    if not args.apply:
        print("\ndry-run: nessuna scrittura (usa --apply)")
        return 0

    print(f"\nchiuse {len(report.closed)} linee")
    return 0


def cmd_prune_missing(args: argparse.Namespace) -> int:
    return _run(_prune_missing(args))


async def _exceptions(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Line, ReviewException

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(ReviewException, Line).join(Line, Line.id == ReviewException.line_id)
            )
        ).all()
        if not rows:
            print("nessuna eccezione")
            return 0
        for record, line in sorted(rows, key=lambda item: item[1].id):
            until = record.review_on.isoformat() if record.review_on else "per sempre"
            print(f"{line.id:>3} · {line.customer.name} · fino al {until} — {record.reason}")
    return 0


def cmd_exceptions(args: argparse.Namespace) -> int:
    return _run(_exceptions(args))


async def _exception_set(args: argparse.Namespace) -> int:
    from datetime import date

    from app.db import session_scope
    from app.services.review_exceptions import ReviewExceptionError, set_exception

    review_on = date.fromisoformat(args.until) if args.until else None
    try:
        async with session_scope() as session:
            record = await set_exception(
                session,
                line_id=args.line,
                reason=args.reason,
                review_on=review_on,
            )
            until = record.review_on.isoformat() if record.review_on else "per sempre"
            print(f"eccezione sulla linea {record.line_id} · fino al {until} — {record.reason}")
    except ReviewExceptionError as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_exception_set(args: argparse.Namespace) -> int:
    return _run(_exception_set(args))


async def _exception_remove(args: argparse.Namespace) -> int:
    from app.db import session_scope
    from app.services.review_exceptions import ReviewExceptionError, clear_exception

    try:
        async with session_scope() as session:
            await clear_exception(session, line_id=args.line)
            print(f"eccezione rimossa dalla linea {args.line}")
    except ReviewExceptionError as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_exception_remove(args: argparse.Namespace) -> int:
    return _run(_exception_remove(args))


async def _resellers(_args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Line, Reseller
    from app.services.text import format_eur

    async with session_scope() as session:
        records = list((await session.scalars(select(Reseller).order_by(Reseller.name))).all())
        if not records:
            print("nessun rivenditore registrato")
            return 0
        for record in records:
            lines = list(
                (
                    await session.scalars(
                        select(Line).where(Line.reseller_id == record.id, Line.deleted_at.is_(None))
                    )
                ).all()
            )
            monthly = sum(line.monthly_price_cents for line in lines)
            telegram = record.telegram_user_id or "non collegato"
            print(
                f"{record.name} · ID {telegram} · {len(lines)} linee · "
                f"{format_eur(monthly)}/mese · attivo={record.active}"
            )
    return 0


def cmd_resellers(args: argparse.Namespace) -> int:
    return _run(_resellers(args))


async def _reseller_set(args: argparse.Namespace) -> int:
    from app.db import session_scope
    from app.services.backfill import BackfillError, ensure_reseller
    from app.services.text import format_eur, parse_price_to_cents

    price = parse_price_to_cents(args.price) if args.price else None
    try:
        async with session_scope() as session:
            record = await ensure_reseller(
                session,
                name=args.name,
                telegram_user_id=args.telegram_id,
                default_price_cents=price,
            )
            telegram = record.telegram_user_id or "non collegato"
            default = (
                format_eur(record.default_monthly_price_cents)
                if record.default_monthly_price_cents is not None
                else "nessun default"
            )
            print(f"rivenditore {record.id} · {record.name} · ID {telegram} · {default}")
    except BackfillError as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_reseller_set(args: argparse.Namespace) -> int:
    return _run(_reseller_set(args))


async def _reseller_group(args: argparse.Namespace) -> int:
    from app.db import session_scope
    from app.services.requests import (
        RequestError,
        link_group,
        reseller_by_name,
        unlink_group,
    )

    async with session_scope() as session:
        if args.unlink:
            reseller = await unlink_group(session, chat_id=args.chat_id)
            if reseller is None:
                print(f"nessun rivenditore collegato a {args.chat_id}", file=sys.stderr)
                return 1
            print(f"gruppo {args.chat_id} scollegato da {reseller.name}")
            return 0

        if not args.name:
            print("serve --name (oppure --unlink)", file=sys.stderr)
            return 1
        reseller = await reseller_by_name(session, args.name)
        if reseller is None:
            print(f"rivenditore «{args.name}» non trovato", file=sys.stderr)
            return 1
        try:
            linked = await link_group(
                session,
                reseller_id=reseller.id,
                chat_id=args.chat_id,
                title=args.title,
            )
        except RequestError as exc:
            print(f"errore: {exc}", file=sys.stderr)
            return 1
        print(f"gruppo {linked.group_chat_id} · {linked.group_title or '-'} → {linked.name}")
    return 0


def cmd_reseller_group(args: argparse.Namespace) -> int:
    return _run(_reseller_group(args))


async def _line_restore(args: argparse.Namespace) -> int:
    from app.db import session_scope
    from app.services.queries import restore_line

    async with session_scope() as session:
        line = await restore_line(session, args.line)
        if line is None:
            print(f"linea {args.line} non trovata", file=sys.stderr)
            return 1
        print(f"linea {line.id} · {line.customer.name} riattivata · scade {line.expires_on}")
    return 0


def cmd_line_restore(args: argparse.Namespace) -> int:
    return _run(_line_restore(args))


async def _line_set(args: argparse.Namespace) -> int:
    from app.db import session_scope
    from app.services.backfill import BackfillError, apply_backfill, parse_rows

    if args.price is None and args.reseller is None:
        print("serve --price oppure --reseller", file=sys.stderr)
        return 1
    async with session_scope() as session:
        from app.models import Line

        line = await session.get(Line, args.line)
        if line is None or line.deleted_at is not None:
            print(f"linea {args.line} non trovata", file=sys.stderr)
            return 1
        reseller = args.reseller if args.reseller else (line.reseller.name if line.reseller else "")
        price = args.price if args.price is not None else line.monthly_price_cents / 100
        try:
            rows = parse_rows(f"{args.line};{reseller};{price}")
            report = await apply_backfill(session, rows, apply=True)
        except BackfillError as exc:
            print(f"errore: {exc}", file=sys.stderr)
            return 1
        for change in report.changes:
            print(change.describe())
    return 0


def cmd_line_set(args: argparse.Namespace) -> int:
    return _run(_line_set(args))


async def _backfill(args: argparse.Namespace) -> int:
    from pathlib import Path as _Path

    from app.db import session_scope
    from app.services.backfill import BackfillError, apply_backfill, read_rows

    path = _Path(args.file)
    if not path.exists():
        print(f"file non trovato: {path}", file=sys.stderr)
        return 1
    try:
        rows = read_rows(path)
    except BackfillError as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1

    async with session_scope() as session:
        report = await apply_backfill(session, rows, apply=args.apply)

    for change in report.changes:
        print(change.describe())
    if report.unknown_lines:
        print(f"linee ignorate (non trovate): {report.unknown_lines}", file=sys.stderr)
    for error in report.errors:
        print(f"errore: {error}", file=sys.stderr)
    print(
        f"\n{len(report.changes)} linee · "
        + ("applicato" if args.apply else "dry-run (usa --apply)")
    )
    return 0 if not report.errors else 1


def cmd_backfill(args: argparse.Namespace) -> int:
    return _run(_backfill(args))


async def _customer_rename(args: argparse.Namespace) -> int:
    from app.db import session_scope
    from app.services.backfill import BackfillError, rename_customer

    try:
        async with session_scope() as session:
            customer = await rename_customer(session, line_id=args.line, name=args.name)
            print(f"cliente {customer.id} · {customer.name}")
    except BackfillError as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_customer_rename(args: argparse.Namespace) -> int:
    return _run(_customer_rename(args))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("migrate", help="applica le migrazioni Alembic").set_defaults(
        func=cmd_migrate
    )
    subparsers.add_parser("backup", help="copia consistente del database").set_defaults(
        func=cmd_backup
    )
    subparsers.add_parser("check", help="configurazione e conteggi").set_defaults(func=cmd_check)
    subparsers.add_parser("seed-demo", help="due clienti finti per una prova locale").set_defaults(
        func=cmd_seed_demo
    )

    sync_parser = subparsers.add_parser("sync", help="sincronizza le linee dai pannelli")
    sync_parser.add_argument(
        "--platform",
        choices=["all", "8plus", "lucky", "vod"],
        default="all",
        help="quale pannello leggere (default: tutti quelli configurati)",
    )
    sync_parser.add_argument(
        "--import",
        dest="import_missing",
        action="store_true",
        help="crea cliente e linea per le righe del pannello non ancora collegate",
    )
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="legge e calcola, senza scrivere nel database",
    )
    sync_parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        metavar="DIR",
        help="usa gli snapshot panel-*.json in DIR invece della rete",
    )
    sync_parser.set_defaults(func=cmd_sync)

    link_parser = subparsers.add_parser(
        "link-panel",
        help="collega o scollega una riga pannello a una linea",
    )
    link_parser.add_argument("--platform", choices=["8plus", "lucky", "vod"], required=True)
    link_parser.add_argument("--username", required=True, help="username/codice nel pannello")
    link_parser.add_argument("--line", type=int, required=True, help="id della linea locale")
    link_parser.add_argument(
        "--unlink",
        action="store_true",
        help="scollega la riga invece di collegarla",
    )
    link_parser.set_defaults(func=cmd_link_panel)

    prune_parser = subparsers.add_parser(
        "prune-missing",
        help="chiude le linee sparite dai pannelli e cancella le righe orfane",
    )
    prune_parser.add_argument(
        "--apply",
        action="store_true",
        help="scrive le modifiche (default: solo anteprima)",
    )
    prune_parser.add_argument(
        "--force",
        action="store_true",
        help="procede anche se le righe sparite superano il 30% di un pannello",
    )
    prune_parser.set_defaults(func=cmd_prune_missing)

    subparsers.add_parser(
        "exceptions",
        help="elenca le anomalie accettate",
    ).set_defaults(func=cmd_exceptions)

    exception_parser = subparsers.add_parser(
        "exception-set",
        help="accetta un'anomalia su una linea e nasconde la review",
    )
    exception_parser.add_argument("--line", type=int, required=True, help="id della linea locale")
    exception_parser.add_argument("--reason", required=True, help="perché l'anomalia è accettata")
    exception_parser.add_argument(
        "--until",
        default=None,
        metavar="AAAA-MM-GG",
        help="quando tornare a segnalarla (default: per sempre)",
    )
    exception_parser.set_defaults(func=cmd_exception_set)

    exception_remove_parser = subparsers.add_parser(
        "exception-remove",
        help="toglie l'eccezione da una linea",
    )
    exception_remove_parser.add_argument("--line", type=int, required=True)
    exception_remove_parser.set_defaults(func=cmd_exception_remove)

    subparsers.add_parser(
        "resellers", help="elenca i rivenditori con linee e prezzo mensile"
    ).set_defaults(func=cmd_resellers)

    reseller_parser = subparsers.add_parser(
        "reseller-set",
        help="crea o aggiorna un rivenditore e collega il suo ID Telegram",
    )
    reseller_parser.add_argument("--name", required=True, help="nome, es. Mario")
    reseller_parser.add_argument(
        "--telegram-id", type=int, default=None, help="ID Telegram del rivenditore"
    )
    reseller_parser.add_argument(
        "--price", default=None, help="prezzo mensile proposto per le nuove linee"
    )
    reseller_parser.set_defaults(func=cmd_reseller_set)

    group_parser = subparsers.add_parser(
        "reseller-group",
        help="collega un gruppo Telegram al rivenditore (letture e richieste)",
    )
    group_parser.add_argument(
        "--chat-id", type=int, required=True, help="ID del gruppo, es. -1001234567890"
    )
    group_parser.add_argument("--name", default=None, help="nome del rivenditore")
    group_parser.add_argument("--title", default=None, help="titolo del gruppo")
    group_parser.add_argument("--unlink", action="store_true", help="togli il collegamento")
    group_parser.set_defaults(func=cmd_reseller_group)

    line_parser = subparsers.add_parser(
        "line-set", help="imposta prezzo e rivenditore di una linea"
    )
    line_parser.add_argument("--line", type=int, required=True)
    line_parser.add_argument("--price", default=None, help="prezzo mensile, es. 6 o 6,50")
    line_parser.add_argument(
        "--reseller",
        default=None,
        help="nome del rivenditore, oppure '-' per cliente diretto",
    )
    line_parser.set_defaults(func=cmd_line_set)

    restore_parser = subparsers.add_parser(
        "line-restore", help="riattiva una linea disattivata nel bot"
    )
    restore_parser.add_argument("--line", type=int, required=True)
    restore_parser.set_defaults(func=cmd_line_restore)

    backfill_parser = subparsers.add_parser(
        "backfill",
        help="applica in blocco prezzi e rivenditori da un file linea;rivenditore;prezzo",
    )
    backfill_parser.add_argument("--file", required=True, metavar="CSV")
    backfill_parser.add_argument(
        "--apply", action="store_true", help="scrive le modifiche (default: anteprima)"
    )
    backfill_parser.set_defaults(func=cmd_backfill)

    rename_parser = subparsers.add_parser(
        "customer-rename",
        help="rinomina il cliente a cui appartiene una linea",
    )
    rename_parser.add_argument("--line", type=int, required=True)
    rename_parser.add_argument("--name", required=True)
    rename_parser.set_defaults(func=cmd_customer_rename)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
