"""Tests for the Bestiarium Devices 'Allowed' switch and thermostat-driven devices."""

# ruff: noqa: S101

from collections.abc import AsyncIterator
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.bestiarium_devices.const import (
    CONF_ALLOWED,
    CONF_DEFAULT_PRIORITY,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_LOAD_PRIORITIES,
    CONF_OVERLOAD_FIRST_QUEUE,
    CONF_POWER_SENSOR_ENTITY_ID,
    CONF_SWITCH_ENTITY_ID,
    CONF_THERMOSTAT_DRIVEN,
    DEVICE_TYPE_EXTENDED,
    DEVICE_TYPE_VIRTUAL,
    DOMAIN,
    DOMAIN_BESTIARIUM_MODE,
    EVENT_DEVICE_TURNED_OFF,
    EVENT_DEVICE_TURNED_ON,
    GREEN_INTERVAL_SECONDS,
    LOAD_PRIORITY_ALWAYS_ON,
    LOAD_PRIORITY_OFF,
)

from .conftest import FakeRelays
from .mode_entities import (
    AC_LOAD_LIMITS,
    BESTIARIUM_MODE_SELECT_ENTITY_ID,
    BINARY_SENSOR_GREEN,
    BINARY_SENSOR_OVERLOAD,
    BINARY_SENSOR_RED,
    BINARY_SENSOR_YELLOW,
    GRID_POWER_SENSOR,
    RED_SENSOR_ATTRIBUTES,
)

pytestmark = pytest.mark.usefixtures(
    "enable_custom_integrations", "mode_entities", "grid", "unload_devices"
)

ZONE_SENSORS = {
    "green": BINARY_SENSOR_GREEN,
    "yellow": BINARY_SENSOR_YELLOW,
    "red": BINARY_SENSOR_RED,
    "overload": BINARY_SENSOR_OVERLOAD,
}
PRIORITIES = {CONF_DEFAULT_PRIORITY: 500, "Always": LOAD_PRIORITY_ALWAYS_ON, "Off": 0}


def _set_zone(hass: HomeAssistant, zone: str) -> None:
    for name, entity_id in ZONE_SENSORS.items():
        hass.states.async_set(
            entity_id,
            STATE_ON if name == zone else STATE_OFF,
            RED_SENSOR_ATTRIBUTES if name == "red" else None,
        )


def _foreign_relay(
    hass: HomeAssistant, name: str, area: str | None = "Bathroom"
) -> tuple[dr.DeviceEntry, str]:
    """Register a relay switch on a device owned by another integration."""
    shelly = MockConfigEntry(domain="shelly")
    shelly.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=shelly.entry_id,
        identifiers={("shelly", name)},
        name=name,
    )
    if area:
        area_id = ar.async_get(hass).async_get_or_create(area).id
        device = dr.async_get(hass).async_update_device(device.id, area_id=area_id)
    relay = er.async_get(hass).async_get_or_create(
        "switch",
        "shelly",
        f"{name}_relay",
        config_entry=shelly,
        device_id=device.id,
    )
    return device, relay.entity_id


async def _setup_device(  # noqa: PLR0913
    hass: HomeAssistant,
    relays: FakeRelays,
    relay_entity_id: str,
    *,
    name: str = "Heating floor",
    relay_on: bool = False,
    device_id: str | None = None,
    device_type: str = DEVICE_TYPE_EXTENDED,
    priorities: dict[str, int] | None = None,
    thermostat_driven: bool | None = None,
    first_queue: bool = False,
) -> MockConfigEntry:
    """Set up a bestiarium_devices entry shaped like the owner's entries."""
    relays.add(relay_entity_id, on=relay_on)
    data = {
        CONF_DEVICE_TYPE: device_type,
        CONF_DEVICE_NAME: name,
        CONF_SWITCH_ENTITY_ID: relay_entity_id,
        CONF_POWER_SENSOR_ENTITY_ID: f"sensor.{relay_entity_id.split('.')[1]}_power",
        CONF_LOAD_PRIORITIES: priorities or PRIORITIES,
        CONF_OVERLOAD_FIRST_QUEUE: first_queue,
    }
    if device_id:
        data[CONF_DEVICE_ID] = device_id
    if thermostat_driven is not None:
        data[CONF_THERMOSTAT_DRIVEN] = thermostat_driven
    entry = MockConfigEntry(domain=DOMAIN, title=name, data=data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _allowed_entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, f"{entry.entry_id}_allowed"
    )
    assert entity_id is not None
    return entity_id


async def _set_allowed(
    hass: HomeAssistant, entry: MockConfigEntry, *, allowed: bool
) -> None:
    await hass.services.async_call(
        "switch",
        "turn_on" if allowed else "turn_off",
        {"entity_id": _allowed_entity_id(hass, entry)},
        blocking=True,
    )
    await hass.async_block_till_done()


async def _run_green_cycles(hass: HomeAssistant, cycles: int = 2) -> None:
    for cycle in range(1, cycles + 1):
        async_fire_time_changed(
            hass,
            dt_util.utcnow() + timedelta(seconds=cycle * (GREEN_INTERVAL_SECONDS + 1)),
        )
        await hass.async_block_till_done()


async def _select_mode(hass: HomeAssistant, mode: str) -> None:
    hass.states.async_set(BESTIARIUM_MODE_SELECT_ENTITY_ID, mode)
    await hass.async_block_till_done()


def _record(hass: HomeAssistant, event_type: str) -> list[Event]:
    events: list[Event] = []

    @callback
    def record(event: Event) -> None:
        events.append(event)

    hass.bus.async_listen(event_type, record)
    return events


@pytest.fixture
async def unload_devices(hass: HomeAssistant) -> AsyncIterator[None]:
    """Remove device entries after the test so the balancer timers stop."""
    yield
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_remove(entry.entry_id)


@pytest.fixture
def grid(hass: HomeAssistant) -> None:
    """Add a bestiarium_mode entry whose grid sensor leaves room for any device."""
    hass.states.async_set(GRID_POWER_SENSOR, "0")
    MockConfigEntry(
        domain=DOMAIN_BESTIARIUM_MODE, data={"ac_load_limits": AC_LOAD_LIMITS}
    ).add_to_hass(hass)


@pytest.fixture
def green_zone(hass: HomeAssistant, mode_entities: None) -> None:  # noqa: ARG001
    """Start in the green zone, mode 'Grid on' (default priorities)."""
    _set_zone(hass, "green")
    hass.states.async_set(BESTIARIUM_MODE_SELECT_ENTITY_ID, "Grid on")


@pytest.mark.usefixtures("green_zone")
class TestAllowedSwitchPlacement:
    """Where the 'Allowed' switch lives."""

    async def test_on_the_relay_device_with_its_area(
        self, hass: HomeAssistant, relays: FakeRelays
    ) -> None:
        """Extended device: the switch sits on the relay's device, in its area."""
        device, relay = _foreign_relay(hass, "Heating floor")
        devices_before = len(list(dr.async_get(hass).devices))
        entry = await _setup_device(hass, relays, relay, device_id=device.id)

        entity_id = _allowed_entity_id(hass, entry)
        registry_entry = er.async_get(hass).async_get(entity_id)
        assert entity_id == "switch.bathroom_heating_floor_allowed"
        assert registry_entry.device_id == device.id
        assert registry_entry.area_id is None
        assert len(list(dr.async_get(hass).devices)) == devices_before
        state = hass.states.get(entity_id)
        assert state.state == STATE_ON
        assert state.attributes["friendly_name"] == "Heating floor Allowed"

    async def test_relay_without_device(
        self, hass: HomeAssistant, relays: FakeRelays, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A relay with no device (or no registry entry) gets a standalone switch."""
        entry = await _setup_device(hass, relays, "switch.oven", name="Oven")

        entity_id = _allowed_entity_id(hass, entry)
        assert entity_id == "switch.oven_allowed"
        assert er.async_get(hass).async_get(entity_id).device_id is None
        assert hass.states.get(entity_id).attributes["friendly_name"] == "Oven Allowed"
        assert [
            r.getMessage()
            for r in caplog.records
            if r.levelname in ("WARNING", "ERROR")
            and r.name.startswith(
                ("custom_components.bestiarium", "homeassistant.helpers.entity")
            )
        ] == []

    async def test_virtual_device_keeps_its_own_device(
        self, hass: HomeAssistant, relays: FakeRelays
    ) -> None:
        """Virtual device: the switch sits on the Bestiarium device of the entry."""
        entry = await _setup_device(
            hass,
            relays,
            "switch.garland",
            name="Garland",
            device_type=DEVICE_TYPE_VIRTUAL,
        )

        device = dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, entry.entry_id), entry.entry_id
        )
        assert device is not None
        assert device.name == "Garland"
        assert device.model == "Virtual Device"
        assert (
            er.async_get(hass).async_get(_allowed_entity_id(hass, entry)).device_id
            == device.id
        )
        assert (
            len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id))
            == 1
        )


class TestAllowedSwitch:
    """Turning 'Allowed' off and on."""

    @pytest.mark.usefixtures("green_zone")
    async def test_disallow_turns_relay_off_and_keeps_it_off(
        self, hass: HomeAssistant, relays: FakeRelays
    ) -> None:
        """Criterion 1: off at once, never turned on by the green strategy."""
        entry = await _setup_device(hass, relays, "switch.floor", relay_on=True)
        turned_off = _record(hass, EVENT_DEVICE_TURNED_OFF)

        await _set_allowed(hass, entry, allowed=False)
        assert not relays.is_on("switch.floor")
        assert hass.states.get(_allowed_entity_id(hass, entry)).state == STATE_OFF
        assert [e.data["strategy"] for e in turned_off] == ["allowed"]

        await _run_green_cycles(hass)
        assert not relays.is_on("switch.floor")

    @pytest.mark.usefixtures("green_zone")
    async def test_disallowed_beats_always_on_mode(
        self, hass: HomeAssistant, relays: FakeRelays
    ) -> None:
        """Criterion 2: an 'always on' mode leaves a disallowed relay off."""
        entry = await _setup_device(hass, relays, "switch.floor")
        await _set_allowed(hass, entry, allowed=False)

        await _select_mode(hass, "Always")

        assert ("turn_on", "switch.floor") not in relays.calls

    @pytest.mark.usefixtures("green_zone")
    async def test_allow_in_green_turns_relay_on_at_once(
        self, hass: HomeAssistant, relays: FakeRelays
    ) -> None:
        """Criterion 3: re-allowing in the green zone turns the relay on."""
        entry = await _setup_device(hass, relays, "switch.floor")
        await _set_allowed(hass, entry, allowed=False)
        turned_on = _record(hass, EVENT_DEVICE_TURNED_ON)

        await _set_allowed(hass, entry, allowed=True)

        assert relays.is_on("switch.floor")
        assert [e.data["strategy"] for e in turned_on] == ["allowed"]

    @pytest.mark.usefixtures("green_zone")
    async def test_allow_in_green_waits_for_room(
        self, hass: HomeAssistant, relays: FakeRelays
    ) -> None:
        """Re-allowing without room below the red zone waits for a green cycle."""
        entry = await _setup_device(hass, relays, "switch.floor")
        await _set_allowed(hass, entry, allowed=False)
        # Nothing known about the floor: 2900 W + 2000 W > 4250 W
        hass.states.async_set(GRID_POWER_SENSOR, "2900")

        await _set_allowed(hass, entry, allowed=True)
        assert not relays.is_on("switch.floor")

        hass.states.async_set(GRID_POWER_SENSOR, "1000")
        await _run_green_cycles(hass, cycles=1)
        assert relays.is_on("switch.floor")

    @pytest.mark.usefixtures("green_zone")
    async def test_allow_in_green_respects_off_mode_priority(
        self, hass: HomeAssistant, relays: FakeRelays
    ) -> None:
        """Re-allowing does not turn on a device whose mode priority is 0."""
        entry = await _setup_device(hass, relays, "switch.floor")
        await _set_allowed(hass, entry, allowed=False)
        await _select_mode(hass, "Off")

        await _set_allowed(hass, entry, allowed=True)

        assert not relays.is_on("switch.floor")

    @pytest.mark.parametrize("zone", ["yellow", "red"])
    async def test_allow_outside_green_waits_for_its_turn(
        self, hass: HomeAssistant, relays: FakeRelays, zone: str
    ) -> None:
        """Criterion 4: no immediate turn-on outside green; normal turn later."""
        _set_zone(hass, zone)
        hass.states.async_set(BESTIARIUM_MODE_SELECT_ENTITY_ID, "Grid on")
        entry = await _setup_device(hass, relays, "switch.floor")
        await _set_allowed(hass, entry, allowed=False)

        await _set_allowed(hass, entry, allowed=True)
        assert not relays.is_on("switch.floor")

        _set_zone(hass, "green")
        await hass.async_block_till_done()
        await _run_green_cycles(hass, cycles=1)
        assert relays.is_on("switch.floor")

    @pytest.mark.usefixtures("green_zone")
    async def test_manually_on_disallowed_relay(
        self, hass: HomeAssistant, relays: FakeRelays
    ) -> None:
        """Criterion 5: green leaves it alone; red sheds it like any other."""
        entry = await _setup_device(hass, relays, "switch.floor")
        await _set_allowed(hass, entry, allowed=False)
        relays.add("switch.floor", on=True)

        await _run_green_cycles(hass)
        assert relays.is_on("switch.floor")

        _set_zone(hass, "red")
        await hass.async_block_till_done()
        assert not relays.is_on("switch.floor")

    @pytest.mark.usefixtures("green_zone")
    async def test_manually_on_disallowed_relay_is_shed_on_overload(
        self, hass: HomeAssistant, relays: FakeRelays
    ) -> None:
        """Criterion 5: overload sheds a manually-on disallowed relay."""
        entry = await _setup_device(hass, relays, "switch.floor", first_queue=True)
        await _set_allowed(hass, entry, allowed=False)
        relays.add("switch.floor", on=True)

        with patch(
            "custom_components.bestiarium_devices.strategies.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            _set_zone(hass, "overload")
            await hass.async_block_till_done()

        assert not relays.is_on("switch.floor")

    @pytest.mark.usefixtures("green_zone")
    async def test_disallowed_survives_reload(
        self, hass: HomeAssistant, relays: FakeRelays
    ) -> None:
        """Criterion 6: still disallowed after a reload (as after a restart)."""
        entry = await _setup_device(hass, relays, "switch.floor")
        await _set_allowed(hass, entry, allowed=False)

        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.options[CONF_ALLOWED] is False
        assert hass.states.get(_allowed_entity_id(hass, entry)).state == STATE_OFF
        await _run_green_cycles(hass)
        assert not relays.is_on("switch.floor")

    @pytest.mark.usefixtures("green_zone")
    async def test_toggling_does_not_reload_the_entry(
        self, hass: HomeAssistant, relays: FakeRelays
    ) -> None:
        """Toggling 'Allowed' updates the entry options without a reload."""
        entry = await _setup_device(hass, relays, "switch.floor")

        with (
            patch.object(hass.config_entries, "async_reload") as reload,
            patch.object(hass.config_entries, "async_schedule_reload") as schedule,
        ):
            await _set_allowed(hass, entry, allowed=False)

        reload.assert_not_called()
        schedule.assert_not_called()


class TestThermostatDriven:
    """Devices switched on by their own thermostat."""

    @pytest.mark.usefixtures("green_zone")
    async def test_never_turned_on_in_green(
        self, hass: HomeAssistant, relays: FakeRelays
    ) -> None:
        """Criterion 7: not turned on, and it does not block other devices."""
        await _setup_device(
            hass,
            relays,
            "switch.floor",
            priorities={CONF_DEFAULT_PRIORITY: 9000},
            thermostat_driven=True,
        )
        await _setup_device(
            hass,
            relays,
            "switch.garland",
            name="Garland",
            priorities={CONF_DEFAULT_PRIORITY: 100},
        )

        await _run_green_cycles(hass)

        assert not relays.is_on("switch.floor")
        assert relays.is_on("switch.garland")

    @pytest.mark.usefixtures("green_zone")
    async def test_shed_in_red_and_not_restored_in_green(
        self, hass: HomeAssistant, relays: FakeRelays
    ) -> None:
        """Criterion 8: red turns it off; green does not turn it back on."""
        await _setup_device(
            hass, relays, "switch.floor", relay_on=True, thermostat_driven=True
        )

        _set_zone(hass, "red")
        await hass.async_block_till_done()
        assert not relays.is_on("switch.floor")

        _set_zone(hass, "green")
        await hass.async_block_till_done()
        await _run_green_cycles(hass)
        assert not relays.is_on("switch.floor")

    @pytest.mark.usefixtures("green_zone")
    async def test_always_on_mode_does_not_turn_it_on(
        self, hass: HomeAssistant, relays: FakeRelays
    ) -> None:
        """Criterion 9: an 'always on' mode leaves it off."""
        await _setup_device(hass, relays, "switch.floor", thermostat_driven=True)

        await _select_mode(hass, "Always")

        assert ("turn_on", "switch.floor") not in relays.calls

    @pytest.mark.usefixtures("green_zone")
    async def test_off_mode_still_turns_it_off(
        self, hass: HomeAssistant, relays: FakeRelays
    ) -> None:
        """A mode priority of 0 turns a thermostat-driven relay off, as before."""
        await _setup_device(
            hass, relays, "switch.floor", relay_on=True, thermostat_driven=True
        )

        await _select_mode(hass, "Off")

        assert not relays.is_on("switch.floor")

    @pytest.mark.usefixtures("green_zone")
    async def test_shed_in_first_queue_on_overload(
        self, hass: HomeAssistant, relays: FakeRelays
    ) -> None:
        """Criterion 10: first-queue thermostat device goes off on overload."""
        await _setup_device(
            hass,
            relays,
            "switch.floor",
            relay_on=True,
            thermostat_driven=True,
            first_queue=True,
        )

        with patch(
            "custom_components.bestiarium_devices.strategies.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            _set_zone(hass, "overload")
            await hass.async_block_till_done()

        assert relays.calls[0] == ("turn_off", "switch.floor")

    @pytest.mark.usefixtures("green_zone")
    async def test_thermostat_device_reallowed_in_green_stays_off(
        self, hass: HomeAssistant, relays: FakeRelays
    ) -> None:
        """Re-allowing a thermostat-driven device does not turn it on."""
        entry = await _setup_device(
            hass, relays, "switch.floor", thermostat_driven=True
        )
        await _set_allowed(hass, entry, allowed=False)

        await _set_allowed(hass, entry, allowed=True)

        assert not relays.is_on("switch.floor")


@pytest.mark.usefixtures("green_zone")
async def test_existing_entry_defaults(hass: HomeAssistant, relays: FakeRelays) -> None:
    """Criterion 11: entries without the new keys: allowed, not thermostat-driven."""
    entry = await _setup_device(hass, relays, "switch.floor")

    assert CONF_THERMOSTAT_DRIVEN not in entry.data
    assert entry.options == {}
    assert hass.states.get(_allowed_entity_id(hass, entry)).state == STATE_ON
    await _run_green_cycles(hass, cycles=1)
    assert relays.is_on("switch.floor")


@pytest.mark.usefixtures("green_zone")
async def test_priority_off_device_with_allowed_on_is_not_turned_on(
    hass: HomeAssistant, relays: FakeRelays
) -> None:
    """Sanity: allowed devices still follow mode priority 0 (unchanged rule)."""
    await _setup_device(
        hass,
        relays,
        "switch.floor",
        priorities={CONF_DEFAULT_PRIORITY: LOAD_PRIORITY_OFF},
    )

    await _run_green_cycles(hass)

    assert not relays.is_on("switch.floor")
