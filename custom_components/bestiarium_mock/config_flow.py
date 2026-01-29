"""Config flow for Bestiarium Mock Devices."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries

from .const import DOMAIN, LOGGER


class BestiariumMockFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Bestiarium Mock Devices."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow initialized by the user."""
        # Only one instance allowed
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            LOGGER.debug("Creating Bestiarium Mock Devices entry")
            return self.async_create_entry(
                title="Mock Devices",
                data={},
            )

        # Show confirmation form
        return self.async_show_form(
            step_id="user",
            data_schema=None,
            description_placeholders={
                "device_count": "10",
            },
        )
