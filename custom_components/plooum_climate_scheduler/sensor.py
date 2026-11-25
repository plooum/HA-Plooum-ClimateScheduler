"""One sensor per program: the temperature its current slot asks for."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ClimateSchedulerConfigEntry
from .engine import Scheduler
from .entity import ProgramEntity, setup_program_entities
from .schedule import OFF


async def async_setup_entry(
    hass: HomeAssistant, entry: ClimateSchedulerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    setup_program_entities(hass, entry, async_add_entities, ScheduledTemperatureSensor)


class ScheduledTemperatureSensor(ProgramEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, scheduler: Scheduler, program_id: str) -> None:
        super().__init__(scheduler, program_id, "sensor")
        self._attr_native_unit_of_measurement = scheduler.hass.config.units.temperature_unit

    @property
    def native_value(self) -> float | None:
        value = self.runner.slot.value
        return None if value in (None, OFF) else float(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.runner.status()
        return {
            **super().extra_state_attributes,
            "scheduled": status["value"],
            "profile": status["profile"],
            "next_change": status["next_change"],
            "next_value": status["next_value"],
            "overrides": status["overrides"],
        }
