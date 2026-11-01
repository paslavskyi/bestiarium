"""The actions of bestiarium_mode."""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import ATTR_SOURCE, DOMAIN, SERVICE_TRIGGER_OVERLOAD

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the actions, which exist with or without a loaded entry."""

    @callback
    def trigger_overload(call: ServiceCall) -> None:
        # One entry at most: its unique id is the domain
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="not_loaded"
            )
        if (tracker := entries[0].runtime_data) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_load_limits"
            )
        tracker.async_trigger_overload(call.data[ATTR_SOURCE])

    hass.services.async_register(
        DOMAIN,
        SERVICE_TRIGGER_OVERLOAD,
        trigger_overload,
        schema=vol.Schema({vol.Required(ATTR_SOURCE): cv.string}),
    )
