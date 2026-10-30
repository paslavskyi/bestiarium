"""The entry of Bestiarium IR and its subentries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr

from custom_components.bestiarium_ir.const import (
    CONF_ZHA_DEVICE,
    DOMAIN,
    SUBENTRY_TYPE_BLASTER,
)

from .conftest import ir_entry, set_up

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_one_entry_only(hass: HomeAssistant) -> None:
    """The entry is made at once, without fields; a second one is refused."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bestiarium IR"
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def _add_blaster(
    hass: HomeAssistant, entry: MockConfigEntry, device_id: str
) -> dict:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_BLASTER), context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    return await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_ZHA_DEVICE: device_id}
    )


async def test_add_blaster_names_it_after_the_zha_device(
    hass: HomeAssistant, zha_device: dr.DeviceEntry
) -> None:
    """The subentry is named as the TS1201 in ZHA (the person's name first)."""
    dr.async_get(hass).async_update_device(zha_device.id, name_by_user="Stasi IR")
    entry = ir_entry()
    await set_up(hass, entry)
    result = await _add_blaster(hass, entry, zha_device.id)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    (subentry,) = entry.subentries.values()
    assert subentry.title == "Stasi IR"
    assert subentry.data == {CONF_ZHA_DEVICE: zha_device.id}


async def test_blaster_device_twice_is_refused(
    hass: HomeAssistant, zha_device: dr.DeviceEntry
) -> None:
    """One TS1201, one blaster."""
    entry = ir_entry()
    await set_up(hass, entry)
    await _add_blaster(hass, entry, zha_device.id)
    result = await _add_blaster(hass, entry, zha_device.id)
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_blaster_to_another_device(
    hass: HomeAssistant, zha_device: dr.DeviceEntry
) -> None:
    """A blaster is moved to another TS1201 without removing it."""
    other = dr.async_get(hass).async_get_or_create(
        config_entry_id=zha_device.config_entry_id,
        identifiers={("zha", "77:66:55:44:33:22:11:00")},
        model="TS1201",
        name="IR bedroom",
    )
    entry = ir_entry()
    await set_up(hass, entry)
    await _add_blaster(hass, entry, zha_device.id)
    (subentry_id,) = entry.subentries
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_BLASTER),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_ZHA_DEVICE: other.id}
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries[subentry_id].data == {CONF_ZHA_DEVICE: other.id}
    assert entry.subentries[subentry_id].title == "IR bedroom"
