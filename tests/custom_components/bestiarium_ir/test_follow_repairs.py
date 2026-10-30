"""A renamed emitter is followed; a gone blaster or emitter is a Repairs issue."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigSubentryData
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.bestiarium_ir.const import (
    CONF_EMITTER,
    CONF_ZHA_DEVICE,
    DOMAIN,
    SUBENTRY_TYPE_BLASTER,
    SUBENTRY_TYPE_HISENSE,
)

from .conftest import RecordingEmitter, hisense_subentry, ir_entry, set_up

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import device_registry as dr
    from pytest_homeassistant_custom_component.common import MockConfigEntry


def _issue(
    hass: HomeAssistant, kind: str, entry: MockConfigEntry
) -> ir.IssueEntry | None:
    (subentry_id,) = entry.subentries
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{kind}_{subentry_id}")


async def test_renamed_emitter_is_followed(
    hass: HomeAssistant,
    ac: tuple[MockConfigEntry, str],
    emitter: RecordingEmitter,  # noqa: ARG001
) -> None:
    """The emitter's new entity_id is written into the air conditioner."""
    entry, _ = ac
    er.async_get(hass).async_update_entity(
        "infrared.test_emitter", new_entity_id="infrared.renamed"
    )
    await hass.async_block_till_done()
    (subentry,) = entry.subentries.values()
    assert subentry.data[CONF_EMITTER] == "infrared.renamed"
    assert _issue(hass, "emitter_gone", entry) is None


async def test_gone_emitter_is_a_repairs_issue_fixed_by_its_form(
    hass: HomeAssistant,
    emitter: RecordingEmitter,  # noqa: ARG001
) -> None:
    """An emitter gone from HA: an issue; its form chooses another one."""
    entry = ir_entry(hisense_subentry("infrared.gone"))
    await set_up(hass, entry)
    issue = _issue(hass, "emitter_gone", entry)
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_placeholders == {
        "name": "Stasi AC",
        "entity": "infrared.gone",
    }

    assert await async_setup_component(hass, "repairs", {})
    flows = hass.data["repairs"]["flow_manager"]
    result = await flows.async_init(DOMAIN, context={"issue_id": issue.issue_id})
    assert result["step_id"] == "init"
    result = await flows.async_configure(
        result["flow_id"], {CONF_EMITTER: "infrared.test_emitter"}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    (subentry,) = entry.subentries.values()
    assert subentry.data[CONF_EMITTER] == "infrared.test_emitter"
    assert _issue(hass, "emitter_gone", entry) is None


async def test_gone_zha_device_is_a_repairs_issue(
    hass: HomeAssistant, zha_device: dr.DeviceEntry
) -> None:
    """A blaster whose TS1201 is gone from ZHA: an issue, fixed by another one."""
    entry = ir_entry(
        ConfigSubentryData(
            data={CONF_ZHA_DEVICE: "gone"},
            subentry_type=SUBENTRY_TYPE_BLASTER,
            title="Old blaster",
            unique_id="gone",
        )
    )
    await set_up(hass, entry)
    issue = _issue(hass, "blaster_device_gone", entry)
    assert issue is not None
    assert issue.translation_placeholders == {"name": "Old blaster"}

    assert await async_setup_component(hass, "repairs", {})
    flows = hass.data["repairs"]["flow_manager"]
    result = await flows.async_init(DOMAIN, context={"issue_id": issue.issue_id})
    result = await flows.async_configure(
        result["flow_id"], {CONF_ZHA_DEVICE: zha_device.id}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert _issue(hass, "blaster_device_gone", entry) is None
    # As its reconfigure form: named as the TS1201, unique by it
    (subentry,) = entry.subentries.values()
    assert (subentry.title, subentry.unique_id) == ("IR black", zha_device.id)


async def test_repair_refuses_a_ts1201_of_another_blaster(
    hass: HomeAssistant, zha_device: dr.DeviceEntry
) -> None:
    """One TS1201, one blaster: the repair cannot point a second one at it."""
    entry = ir_entry(
        ConfigSubentryData(
            data={CONF_ZHA_DEVICE: zha_device.id},
            subentry_type=SUBENTRY_TYPE_BLASTER,
            title="IR black",
            unique_id=zha_device.id,
        ),
        ConfigSubentryData(
            data={CONF_ZHA_DEVICE: "gone"},
            subentry_type=SUBENTRY_TYPE_BLASTER,
            title="Old blaster",
            unique_id="gone",
        ),
    )
    await set_up(hass, entry)
    gone = next(s for s in entry.subentries.values() if s.title == "Old blaster")
    assert await async_setup_component(hass, "repairs", {})
    flows = hass.data["repairs"]["flow_manager"]
    result = await flows.async_init(
        DOMAIN, context={"issue_id": f"blaster_device_gone_{gone.subentry_id}"}
    )
    result = await flows.async_configure(
        result["flow_id"], {CONF_ZHA_DEVICE: zha_device.id}
    )
    assert result["errors"] == {CONF_ZHA_DEVICE: "already_configured"}
    assert entry.subentries[gone.subentry_id].data[CONF_ZHA_DEVICE] == "gone"


async def test_issue_goes_when_fixed_by_reconfigure(
    hass: HomeAssistant,
    emitter: RecordingEmitter,  # noqa: ARG001
) -> None:
    """The setting fixed in the subentry's own form: the issue goes."""
    entry = ir_entry(hisense_subentry("infrared.gone"))
    await set_up(hass, entry)
    assert _issue(hass, "emitter_gone", entry) is not None
    (subentry_id,) = entry.subentries
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_HISENSE),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )
    await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Stasi AC", CONF_EMITTER: "infrared.test_emitter"},
    )
    await hass.async_block_till_done()
    assert _issue(hass, "emitter_gone", entry) is None


async def test_issue_of_a_removed_subentry_goes(
    hass: HomeAssistant,
    emitter: RecordingEmitter,  # noqa: ARG001
) -> None:
    """The air conditioner removed: its issue goes with it."""
    entry = ir_entry(hisense_subentry("infrared.gone"))
    await set_up(hass, entry)
    issue = _issue(hass, "emitter_gone", entry)
    assert issue is not None
    (subentry_id,) = entry.subentries
    hass.config_entries.async_remove_subentry(entry, subentry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue.issue_id) is None
