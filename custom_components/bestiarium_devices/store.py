"""What the balancer remembers per device entry, kept across restarts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

SAVE_DELAY_SECONDS = 10


class EntryStore[T]:
    """
    A value per device entry in a Store: loaded once, saved a little later.

    A subclass says how its value is written (_encode) and read (_decode).
    """

    def __init__(self, hass: HomeAssistant, key: str, version: int = 1) -> None:
        """Start empty; call async_load to read the store."""
        self._store: Store[dict[str, Any]] = Store(hass, version, key)
        self._entries: dict[str, T] = {}

    async def async_load(self) -> None:
        """Read the stored values."""
        stored = await self._store.async_load() or {}
        self._entries = {
            entry_id: self._decode(value) for entry_id, value in stored.items()
        }

    def remove(self, entry_id: str) -> None:
        """Forget a removed device entry."""
        self._entries.pop(entry_id, None)
        self._save()

    def _save(self) -> None:
        self._store.async_delay_save(
            lambda: {
                entry_id: self._encode(value)
                for entry_id, value in self._entries.items()
            },
            SAVE_DELAY_SECONDS,
        )

    def _encode(self, value: T) -> Any:
        raise NotImplementedError

    def _decode(self, value: Any) -> T:
        raise NotImplementedError
