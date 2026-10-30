"""Config flow for Bestiarium IR: one entry, its blasters and air conditioners."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.infrared import DOMAIN as INFRARED_DOMAIN
from homeassistant.components.infrared import async_get_emitters
from homeassistant.config_entries import (
    SOURCE_USER,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector

from .const import (
    CONF_EMITTER,
    CONF_ZHA_DEVICE,
    DOMAIN,
    SUBENTRY_TYPE_BLASTER,
    SUBENTRY_TYPE_HISENSE,
    ZHA_DOMAIN,
    ZHA_MODEL,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant


def device_selector(hass: HomeAssistant, subentry_type: str) -> selector.Selector:
    """Return the selector of what a subentry runs on: a TS1201, or an emitter."""
    if subentry_type == SUBENTRY_TYPE_BLASTER:
        return selector.DeviceSelector(
            selector.DeviceSelectorConfig(
                filter=selector.DeviceFilterSelectorConfig(
                    integration=ZHA_DOMAIN, model=ZHA_MODEL
                )
            )
        )
    return selector.EntitySelector(
        selector.EntitySelectorConfig(
            domain=INFRARED_DOMAIN, include_entities=async_get_emitters(hass)
        )
    )


class BestiariumIrFlowHandler(ConfigFlow, domain=DOMAIN):
    """The entry of Bestiarium IR: made at once, the devices are its subentries."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> ConfigFlowResult:
        """Create the entry."""
        return self.async_create_entry(title="Bestiarium IR", data={})

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls,
        config_entry: ConfigEntry,  # noqa: ARG003
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the kinds of devices."""
        return {
            SUBENTRY_TYPE_BLASTER: BlasterSubentryFlow,
            SUBENTRY_TYPE_HISENSE: HisenseSubentryFlow,
        }


class BlasterSubentryFlow(ConfigSubentryFlow):
    """A Zigbee TS1201 of ZHA as an infrared emitter."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the TS1201."""
        return await self._step("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose another TS1201."""
        return await self._step("reconfigure", user_input)

    async def _step(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        subentry = (
            None if self.source == SOURCE_USER else self._get_reconfigure_subentry()
        )
        if user_input is not None:
            device_id = user_input[CONF_ZHA_DEVICE]
            if self._taken(device_id, subentry):
                return self.async_abort(reason="already_configured")
            device = dr.async_get(self.hass).async_get(device_id)
            title = (device.name_by_user or device.name) if device else device_id
            data = {CONF_ZHA_DEVICE: device_id}
            if subentry is None:
                return self.async_create_entry(
                    title=title, data=data, unique_id=device_id
                )
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                title=title,
                data=data,
                unique_id=device_id,
            )
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ZHA_DEVICE,
                        default=(
                            subentry.data[CONF_ZHA_DEVICE]
                            if subentry
                            else vol.UNDEFINED
                        ),
                    ): device_selector(self.hass, SUBENTRY_TYPE_BLASTER)
                }
            ),
        )

    def _taken(self, device_id: str, subentry: ConfigSubentry | None) -> bool:
        """Return whether another blaster already uses the device."""
        return any(
            other.subentry_type == SUBENTRY_TYPE_BLASTER
            and other.data[CONF_ZHA_DEVICE] == device_id
            and other is not subentry
            for other in self._get_entry().subentries.values()
        )


class HisenseSubentryFlow(ConfigSubentryFlow):
    """A Hisense air conditioner on an infrared emitter of HA."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Name the air conditioner and choose its emitter."""
        return await self._step("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Rename it or choose another emitter."""
        return await self._step("reconfigure", user_input)

    async def _step(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        subentry = (
            None if self.source == SOURCE_USER else self._get_reconfigure_subentry()
        )
        if not async_get_emitters(self.hass):
            return self.async_abort(reason="no_emitters")
        if user_input is not None:
            title = user_input[CONF_NAME].strip()
            data = {CONF_EMITTER: user_input[CONF_EMITTER]}
            if subentry is None:
                return self.async_create_entry(title=title, data=data)
            return self.async_update_and_abort(
                self._get_entry(), subentry, title=title, data=data
            )
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NAME, default=subentry.title if subentry else "Hisense"
                    ): selector.TextSelector(),
                    vol.Required(
                        CONF_EMITTER,
                        default=(
                            subentry.data[CONF_EMITTER] if subentry else vol.UNDEFINED
                        ),
                    ): device_selector(self.hass, SUBENTRY_TYPE_HISENSE),
                }
            ),
        )
