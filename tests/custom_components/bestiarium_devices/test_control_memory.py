"""The response memory: bounded, kept in a Store across restarts."""

# ruff: noqa: S101, PLR2004

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.bestiarium_devices.const import RESPONSE_MEMORY_SIZE
from custom_components.bestiarium_devices.control import ResponseMemory
from custom_components.bestiarium_devices.store import SAVE_DELAY_SECONDS


async def test_longest_of_the_last_replies(hass: HomeAssistant) -> None:
    """The estimate is the longest of the last replies; older ones are dropped."""
    memory = ResponseMemory(hass)
    await memory.async_load()

    memory.add("entry", 40)
    for _ in range(RESPONSE_MEMORY_SIZE):
        memory.add("entry", 3)

    assert memory.longest("entry") == 3
    assert memory.longest("other") is None


async def test_memory_survives_a_restart(
    hass: HomeAssistant, hass_storage: dict
) -> None:
    """Criterion 12: a new memory reads what the last one saved."""
    memory = ResponseMemory(hass)
    await memory.async_load()
    memory.add("entry", 12)
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=SAVE_DELAY_SECONDS + 1)
    )
    await hass.async_block_till_done()

    restarted = ResponseMemory(hass)
    await restarted.async_load()

    assert restarted.longest("entry") == 12
    assert hass_storage["bestiarium_devices.control"]["data"] == {"entry": [12]}


async def test_removed_device_is_forgotten(hass: HomeAssistant) -> None:
    """A removed device entry leaves nothing behind."""
    memory = ResponseMemory(hass)
    await memory.async_load()
    memory.add("entry", 12)

    memory.remove("entry")

    assert memory.longest("entry") is None
