"""The house-wide profile (Default, Holidays…)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ClimateSchedulerConfigEntry
from .const import DOMAIN
from .engine import Scheduler
from .entity import SchedulerEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ClimateSchedulerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([ProfileSelect(entry.runtime_data)])


class ProfileSelect(SchedulerEntity, SelectEntity):
    _attr_translation_key = "profile"
    _attr_unique_id = "profile"

    def __init__(self, scheduler: Scheduler) -> None:
        super().__init__(scheduler)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "scheduler")}, name="Climate scheduler", manufacturer="Plooum", model="Climate scheduler"
        )

    @property
    def options(self) -> list[str]:
        return [p["name"] for p in self.scheduler.profiles]

    @property
    def current_option(self) -> str:
        return self.scheduler.profile_name(self.scheduler.active_profile)

    async def async_select_option(self, option: str) -> None:
        profile = self.scheduler.find_profile(option)
        if profile:
            self.scheduler.set_active_profile(profile)
