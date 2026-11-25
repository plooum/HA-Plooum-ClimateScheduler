"""Plooum Climate Scheduler: week schedules for climate entities, with house-wide profiles.

Programs (a set of thermostats) hold one week per profile; the active profile (Default,
Holidays…) is shared by the whole house. The engine applies the current slot of each program at
each change and respects manual changes until the next one. The schedule card of
HA-Plooum-Cards edits it all through the websocket API (websocket.py).
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType

from . import websocket
from .const import ATTR_PROFILE, DOMAIN, SERVICE_CLEAR_OVERRIDE, SERVICE_SET_PROFILE
from .engine import Scheduler

PLATFORMS = [Platform.SELECT, Platform.SENSOR, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type ClimateSchedulerConfigEntry = ConfigEntry[Scheduler]


def get_scheduler(hass: HomeAssistant) -> Scheduler:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise ServiceValidationError("Plooum Climate Scheduler is not set up")


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    websocket.async_register(hass)

    async def set_profile(call: ServiceCall) -> None:
        scheduler = get_scheduler(hass)
        profile = scheduler.find_profile(call.data[ATTR_PROFILE])
        if profile is None:
            raise ServiceValidationError(f"Unknown profile: {call.data[ATTR_PROFILE]}")
        scheduler.set_active_profile(profile)

    async def clear_override(call: ServiceCall) -> None:
        get_scheduler(hass).clear_overrides(entities=call.data.get(ATTR_ENTITY_ID))

    hass.services.async_register(
        DOMAIN, SERVICE_SET_PROFILE, set_profile, vol.Schema({vol.Required(ATTR_PROFILE): cv.string})
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_OVERRIDE, clear_override, vol.Schema({vol.Optional(ATTR_ENTITY_ID): cv.entity_ids})
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ClimateSchedulerConfigEntry) -> bool:
    scheduler = Scheduler(hass, entry.entry_id)
    await scheduler.async_load()
    entry.runtime_data = scheduler
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def start(_hass: HomeAssistant) -> None:
        # Once Home Assistant runs, so that the thermostats exist (at once if it already runs).
        scheduler.start()

    entry.async_on_unload(async_at_started(hass, start))
    entry.async_on_unload(scheduler.stop)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ClimateSchedulerConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
