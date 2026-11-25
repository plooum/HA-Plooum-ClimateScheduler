"""Base of the entities: the scheduler's (profile select) and each program's (switch, sensor)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ClimateSchedulerConfigEntry
from .const import DOMAIN, SIGNAL_PROGRAM_ADDED
from .engine import PROGRAM_ENTITIES, ProgramRunner, Scheduler


class SchedulerEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, scheduler: Scheduler) -> None:
        self.scheduler = scheduler

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.scheduler.async_add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class ProgramEntity(SchedulerEntity):
    """An entity of a program, on the program's device."""

    def __init__(self, scheduler: Scheduler, program_id: str, platform: str) -> None:
        super().__init__(scheduler)
        self.program_id = program_id
        self._attr_translation_key = PROGRAM_ENTITIES[platform]
        self._attr_unique_id = f"{program_id}_{PROGRAM_ENTITIES[platform]}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, program_id)},
            name=self.runner.program["name"],
            manufacturer="Plooum",
            model="Climate schedule",
        )

    @property
    def runner(self) -> ProgramRunner:
        return self.scheduler.runners[self.program_id]

    @property
    def available(self) -> bool:
        return self.program_id in self.scheduler.runners

    @callback
    def _handle_update(self) -> None:
        if self.program_id in self.scheduler.runners:  # a deleted program's entities are being removed
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"program_id": self.program_id, "climate_entities": list(self.runner.entities)}


def setup_program_entities(
    hass: HomeAssistant,
    entry: ClimateSchedulerConfigEntry,
    add: AddConfigEntryEntitiesCallback,
    factory: Callable[[Scheduler, str], Entity],
) -> None:
    """Add a program entity for every program, now and when one is created."""
    scheduler = entry.runtime_data
    add([factory(scheduler, pid) for pid in scheduler.runners])

    @callback
    def added(program_id: str) -> None:
        add([factory(scheduler, program_id)])

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_PROGRAM_ADDED, added))
