"""One switch per program: is the program running."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ClimateSchedulerConfigEntry
from .engine import Scheduler
from .entity import ProgramEntity, setup_program_entities


async def async_setup_entry(
    hass: HomeAssistant, entry: ClimateSchedulerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    setup_program_entities(hass, entry, async_add_entities, ScheduleSwitch)


class ScheduleSwitch(ProgramEntity, SwitchEntity):
    def __init__(self, scheduler: Scheduler, program_id: str) -> None:
        super().__init__(scheduler, program_id, "switch")

    @property
    def is_on(self) -> bool:
        return self.runner.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.scheduler.set_enabled(self.program_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.scheduler.set_enabled(self.program_id, False)
