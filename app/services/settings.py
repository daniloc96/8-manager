from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Setting


@dataclass(frozen=True, slots=True)
class EditableSetting:
    """Una chiave di `settings` che il bot può scrivere, con la sua validazione."""

    key: str
    label: str
    hint: str

    def validate(self, value: str) -> str:
        raise NotImplementedError


class DomainSetting(EditableSetting):
    def validate(self, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not cleaned:
            raise SettingValueError("il dominio non può essere vuoto.")
        if "://" not in cleaned:
            raise SettingValueError("scrivi l'URL completo, per esempio https://vod.example.org.")
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"}:
            raise SettingValueError("il dominio deve iniziare con http:// o https://.")
        if not parsed.netloc:
            raise SettingValueError("manca il dominio.")
        if parsed.username or parsed.password:
            raise SettingValueError(
                "il dominio non può contenere credenziali: scrivi solo schema e dominio."
            )
        raw_host = parsed.netloc.rsplit("@", 1)[-1]
        if "?" in raw_host or "#" in raw_host:
            raise SettingValueError("il dominio non può contenere ? o #.")
        # urlparse scarta un ? o # finale nella query: va guardata la stringa.
        if any(char in cleaned for char in "?#"):
            raise SettingValueError("il dominio non può contenere ? o #.")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise SettingValueError("scrivi solo schema e dominio, senza percorso né parametri.")
        if any(char in cleaned for char in " <>\"'`"):
            raise SettingValueError("il dominio contiene caratteri non ammessi.")
        return cleaned


class SettingValueError(ValueError):
    """Valore rifiutato dalla validazione della chiave."""


EDITABLE_SETTINGS: dict[str, EditableSetting] = {
    "live_domain": DomainSetting(
        key="live_domain",
        label="Dominio Live",
        hint="Base del link Live consegnato al cliente (get.php); lo aggiorna anche il sync.",
    ),
    "vod_domain": DomainSetting(
        key="vod_domain",
        label="Dominio VOD",
        hint="Base del link VOD consegnato al cliente (get.php); lo aggiorna anche il sync.",
    ),
}


def editable(key: str) -> EditableSetting | None:
    return EDITABLE_SETTINGS.get(key)


def edit_definition(key: str) -> EditableSetting | None:
    """Alias usato dal layer bot: stessa tabella, nome esplicito."""
    return editable(key)


async def read_value(session: AsyncSession, key: str) -> str | None:
    record = await session.get(Setting, key)
    return record.value if record is not None else None


async def set_value(session: AsyncSession, key: str, raw: str | None) -> tuple[str | None, bool]:
    """Salva un valore validato. `None`/vuoto cancella la chiave.

    Restituisce (valore precedente, cambiato).
    """
    definition = editable(key)
    if definition is None:
        raise SettingValueError(f"impostazione non modificabile: {key}.")

    current = await session.get(Setting, key)
    previous = current.value if current is not None else None

    candidate = (raw or "").strip()
    if candidate == "-":
        if current is not None:
            await session.delete(current)
            session.add(
                AuditLog(
                    action="setting_clear",
                    entity="setting",
                    entity_id=0,
                    payload={"key": key, "old": previous},
                )
            )
            await session.flush()
            return previous, True
        return previous, False

    # Un valore vuoto non è «cancella»: è un errore da correggere. Solo `-`
    # riporta la chiave al default, così uno svuotamento accidentale non passa.
    value = definition.validate(candidate)
    if value == previous:
        return previous, False

    if current is None:
        session.add(Setting(key=key, value=value))
    else:
        current.value = value
    session.add(
        AuditLog(
            action="setting_set",
            entity="setting",
            entity_id=0,
            payload={"key": key, "old": previous, "new": value},
        )
    )
    await session.flush()
    return previous, True


async def all_values(session: AsyncSession) -> dict[str, str | None]:
    """Valore effettivo di ogni chiave gestibile, letta dal database."""
    records = {
        record.key: record.value for record in (await session.scalars(select(Setting))).all()
    }
    return {key: records.get(key) for key in EDITABLE_SETTINGS}
