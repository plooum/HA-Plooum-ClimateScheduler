"""The scheduler: stored profiles and programs, and one runner per program that applies its
week to its thermostats and tracks manual changes (overrides)."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime
import logging
import time
from typing import Any
import uuid

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_HVAC_MODES,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_STEP,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Context, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_point_in_time, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import schedule
from .const import DOMAIN, GRACE_SECONDS, SIGNAL_PROGRAM_ADDED, STORAGE_KEY, STORAGE_VERSION
from .schedule import DEFAULT_PROFILE, OFF, Slot, Value

DEFAULT_PALETTE = (15.0, 17.0, 19.0, 21.0)

_LOGGER = logging.getLogger(__name__)

# Slot parameters: entity domain -> kind of value (a switch takes a bool, a number a float).
PARAMETER_DOMAINS = {"switch": "toggle", "input_boolean": "toggle", "number": "number", "input_number": "number"}

# unique_id suffixes of a program's entities, by platform
PROGRAM_ENTITIES = {"switch": "schedule", "sensor": "scheduled_temperature"}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _unavailable(state: State | None) -> bool:
    return state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN)


def _observed(state: State) -> Value | None:
    if state.state == HVACMode.OFF:
        return OFF
    temp = state.attributes.get(ATTR_TEMPERATURE)
    return float(temp) if isinstance(temp, (int, float)) else None


def _same_temperature(state: State, temp: Any, value: Value) -> bool:
    step = state.attributes.get(ATTR_TARGET_TEMP_STEP)
    tolerance = step / 2 if isinstance(step, (int, float)) and step > 0 else 0.05
    return abs(float(temp) - float(value)) <= tolerance + 1e-9


def _matches(state: State, value: Value) -> bool:
    observed = _observed(state)
    if value == OFF or observed == OFF or observed is None:
        return observed == value
    return _same_temperature(state, observed, value)


class ProgramRunner:
    """Applies one program's week to its thermostats."""

    def __init__(
        self, scheduler: Scheduler, program: dict[str, Any], overrides: dict[str, dict], turned_off: set[str] | None = None
    ) -> None:
        self.scheduler = scheduler
        self.hass = scheduler.hass
        self.program = program
        self.overrides: dict[str, dict[str, Any]] = overrides  # entity -> {value, until: datetime | None}
        # Thermostats the engine turned off (an off slot) and has to turn back on. With `self_off`, the engine
        # only turns these on: a thermostat that went off by itself (eco mode, standby) relights by itself.
        self.turned_off: set[str] = (turned_off or set()) & set(program["entities"])
        self.slot = Slot(None)
        self.profile: str | None = None
        self._contexts: deque[str] = deque(maxlen=64)
        self._commanded: dict[str, tuple[Value, float]] = {}  # entity -> (value, monotonic time)
        self._checks: dict[str, CALLBACK_TYPE] = {}
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._unsub_state: CALLBACK_TYPE | None = None
        self._started = False

    @property
    def id(self) -> str:
        return self.program["id"]

    @property
    def entities(self) -> list[str]:
        return self.program["entities"]

    @property
    def enabled(self) -> bool:
        return self.program["enabled"]

    @property
    def self_off(self) -> bool:
        """The thermostats turn themselves off and on (eco mode, standby): the engine leaves that to them."""
        return bool(self.program.get("self_off"))

    @callback
    def _set_turned_off(self, entity: str, off: bool) -> None:
        if off == (entity in self.turned_off):
            return
        if off:
            self.turned_off.add(entity)
        else:
            self.turned_off.discard(entity)
        self.scheduler.save()

    @callback
    def start(self) -> None:
        self._started = True
        if self.entities:
            self._unsub_state = async_track_state_change_event(self.hass, self.entities, self._on_state)
        self.evaluate()

    @callback
    def stop(self) -> None:
        self._started = False
        for unsub in (self._unsub_timer, self._unsub_state, *self._checks.values()):
            if unsub:
                unsub()
        self._unsub_timer = self._unsub_state = None
        self._checks.clear()

    @callback
    def evaluate(self) -> None:
        """Recompute the slot, apply it where no override holds, arm the next change."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        now = dt_util.now()
        self.profile, week = schedule.resolve_week(self.program["profiles"], self.scheduler.active_profile)
        self.slot = schedule.evaluate(week, now) if self.enabled else Slot(None)
        expired = [e for e, o in self.overrides.items() if o["until"] and o["until"] <= now]
        for entity in expired:
            del self.overrides[entity]
        if expired:
            self.scheduler.save()
        if self.slot.value is not None:
            for entity in self.entities:
                if entity not in self.overrides:
                    self.hass.async_create_task(self._apply(entity, self.slot.value))
        for param_id, value in self.slot.params.items():
            param = next((p for p in self.program.get("parameters", []) if p["id"] == param_id), None)
            if param:
                self.hass.async_create_task(self._apply_param(param["entity"], value))
        if self.slot.next_change:
            self._unsub_timer = async_track_point_in_time(self.hass, self._on_change, self.slot.next_change)
        self.scheduler.notify()

    async def _apply_param(self, entity: str, value: bool | float) -> None:
        """Give a slot parameter's entity (eco mode switch, hysteresis number…) its value, if it differs."""
        state = self.hass.states.get(entity)
        if _unavailable(state):
            return
        domain = entity.split(".", 1)[0]
        if isinstance(value, bool):
            if (state.state == "on") != value:
                await self._call("turn_on" if value else "turn_off", {ATTR_ENTITY_ID: entity}, domain)
            return
        number = float(value)
        lo, hi = state.attributes.get("min"), state.attributes.get("max")
        if isinstance(lo, (int, float)) and number < lo:
            number = float(lo)
        if isinstance(hi, (int, float)) and number > hi:
            number = float(hi)
        try:
            if abs(float(state.state) - number) < 1e-6:
                return
        except ValueError:
            pass
        await self._call("set_value", {ATTR_ENTITY_ID: entity, "value": number}, domain)

    @callback
    def _on_change(self, _now: datetime) -> None:
        self._unsub_timer = None
        self.evaluate()

    def _on_mode(self, state: State) -> str:
        modes = state.attributes.get(ATTR_HVAC_MODES) or []
        wanted = self.program.get("hvac_mode")
        if wanted and wanted in modes:
            return wanted
        if not wanted and state.state != HVACMode.OFF and state.state in modes:
            return state.state
        for mode in (HVACMode.HEAT, HVACMode.HEAT_COOL, HVACMode.AUTO):
            if mode in modes:
                return mode
        return next((m for m in modes if m != HVACMode.OFF), HVACMode.HEAT)

    async def _apply(self, entity: str, value: Value) -> None:
        state = self.hass.states.get(entity)
        if _unavailable(state):
            return  # applied when it comes back (_on_state)
        if value == OFF:
            self._commanded[entity] = (OFF, time.monotonic())
            self._set_turned_off(entity, True)
            if state.state != HVACMode.OFF:
                await self._call(SERVICE_SET_HVAC_MODE, {ATTR_ENTITY_ID: entity, ATTR_HVAC_MODE: HVACMode.OFF})
            return
        temp = float(value)
        lo, hi = state.attributes.get(ATTR_MIN_TEMP), state.attributes.get(ATTR_MAX_TEMP)
        if isinstance(lo, (int, float)) and temp < lo:
            temp = float(lo)
        if isinstance(hi, (int, float)) and temp > hi:
            temp = float(hi)
        self._commanded[entity] = (temp, time.monotonic())
        data: dict[str, Any] = {ATTR_ENTITY_ID: entity, ATTR_TEMPERATURE: temp}
        if self._standby(entity, state):
            # Off by itself: only its target, it relights by itself when needed.
            current = state.attributes.get(ATTR_TEMPERATURE)
            if isinstance(current, (int, float)) and _same_temperature(state, current, temp):
                return
            await self._call(SERVICE_SET_TEMPERATURE, data)
            return
        mode = self._on_mode(state)
        if state.state != mode and (state.state == HVACMode.OFF or self.program.get("hvac_mode")):
            data[ATTR_HVAC_MODE] = mode
        elif _matches(state, temp):
            return
        await self._call(SERVICE_SET_TEMPERATURE, data)
        if ATTR_HVAC_MODE in data:
            self._set_turned_off(entity, False)

    def _standby(self, entity: str, state: State) -> bool:
        """Off without the engine having turned it off, on a program whose thermostats do that by themselves."""
        return self.self_off and state.state == HVACMode.OFF and entity not in self.turned_off

    def _conforms(self, entity: str, state: State, expected: Value) -> bool:
        """The thermostat follows the schedule (a standby counts as following a temperature slot)."""
        if expected != OFF and self._standby(entity, state):
            current = state.attributes.get(ATTR_TEMPERATURE)
            return not isinstance(current, (int, float)) or _same_temperature(state, current, expected)
        return _matches(state, expected)

    async def _call(self, service: str, data: dict[str, Any], domain: str = CLIMATE_DOMAIN) -> None:
        context = Context()
        self._contexts.append(context.id)
        _LOGGER.debug("%s: %s.%s %s", self.program["name"], domain, service, data)
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True, context=context)
        except (HomeAssistantError, ValueError) as err:
            _LOGGER.warning("%s: %s.%s %s failed: %s", self.program["name"], domain, service, data, err)

    @callback
    def _on_state(self, event: Event[EventStateChangedData]) -> None:
        if not self.enabled or self.slot.value is None:
            return
        entity, old, new = event.data["entity_id"], event.data["old_state"], event.data["new_state"]
        if _unavailable(new):
            return
        if _unavailable(old):  # back: give it the current slot
            if entity not in self.overrides:
                self.hass.async_create_task(self._apply(entity, self.slot.value))
            return
        if new.state != HVACMode.OFF:
            self._set_turned_off(entity, False)  # on again, whoever did it
        if new.context.id in self._contexts:
            return
        if old.state == new.state and old.attributes.get(ATTR_TEMPERATURE) == new.attributes.get(ATTR_TEMPERATURE):
            return  # another attribute (current temperature, action…)
        commanded = self._commanded.get(entity)
        wait = commanded[1] + GRACE_SECONDS - time.monotonic() if commanded else 0
        if wait > 0:
            if entity not in self._checks:
                self._checks[entity] = async_call_later(self.hass, wait, self._delayed_check(entity))
            return
        self._check(entity)

    def _delayed_check(self, entity: str) -> Callable[[datetime], None]:
        @callback
        def check(_now: datetime) -> None:
            self._checks.pop(entity, None)
            if self._started and self.enabled and self.slot.value is not None:
                self._check(entity)

        return check

    @callback
    def _check(self, entity: str) -> None:
        """Compare a thermostat with the schedule: a difference is an override until the next change."""
        state = self.hass.states.get(entity)
        if _unavailable(state):
            return
        commanded = self._commanded.get(entity)
        expected = commanded[0] if commanded else self.slot.value
        if self._conforms(entity, state, expected):
            if self.overrides.pop(entity, None) is None:
                return
            _LOGGER.debug("%s: %s back on schedule", self.program["name"], entity)
        else:
            standby = self._standby(entity, state) and isinstance(state.attributes.get(ATTR_TEMPERATURE), (int, float))
            value = float(state.attributes[ATTR_TEMPERATURE]) if standby else _observed(state)
            self.overrides[entity] = {"value": value, "until": self.slot.next_change}
            _LOGGER.debug("%s: %s overridden until %s", self.program["name"], entity, self.slot.next_change)
        self.scheduler.save()
        self.scheduler.notify()

    @callback
    def clear_overrides(self, entities: list[str] | None = None) -> bool:
        cleared = [e for e in list(self.overrides) if entities is None or e in entities]
        for entity in cleared:
            del self.overrides[entity]
        return bool(cleared)

    def status(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "value": self.slot.value,
            "next_change": self.slot.next_change.isoformat() if self.slot.next_change else None,
            "next_value": self.slot.next_value,
            "params": dict(self.slot.params),
            "overrides": {
                e: {"value": o["value"], "until": o["until"].isoformat() if o["until"] else None}
                for e, o in self.overrides.items()
            },
        }


class Scheduler:
    """Stored profiles and programs; the runners."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.profiles: list[dict[str, str]] = [{"id": DEFAULT_PROFILE, "name": "Default"}]
        self.active_profile = DEFAULT_PROFILE
        self.palette: list[float] = list(DEFAULT_PALETTE)
        self.runners: dict[str, ProgramRunner] = {}
        self._listeners: list[Callable[[], None]] = []
        self._started = False

    # ----- storage -----

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.profiles = data.get("profiles") or self.profiles
        ids = {p["id"] for p in self.profiles}
        self.active_profile = data.get("active_profile") if data.get("active_profile") in ids else DEFAULT_PROFILE
        self.palette = data.get("palette", self.palette)
        overrides = data.get("overrides", {})
        turned_off = data.get("turned_off", {})
        for program in data.get("programs", []):
            saved = {
                e: {"value": o["value"], "until": dt_util.parse_datetime(o["until"]) if o.get("until") else None}
                for e, o in overrides.get(program["id"], {}).items()
                if e in program["entities"]
            }
            self.runners[program["id"]] = ProgramRunner(self, program, saved, set(turned_off.get(program["id"], [])))

    @callback
    def save(self) -> None:
        self._store.async_delay_save(self._data_to_save, 1)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {
            **self.config(),
            "overrides": {
                pid: {
                    e: {"value": o["value"], "until": o["until"].isoformat() if o["until"] else None}
                    for e, o in r.overrides.items()
                }
                for pid, r in self.runners.items()
                if r.overrides
            },
            "turned_off": {pid: sorted(r.turned_off) for pid, r in self.runners.items() if r.turned_off},
        }

    # ----- lifecycle -----

    @callback
    def start(self) -> None:
        self._started = True
        for runner in self.runners.values():
            runner.start()

    @callback
    def stop(self) -> None:
        self._started = False
        for runner in self.runners.values():
            runner.stop()

    # ----- listeners (entities, websocket subscriptions) -----

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    # ----- reading -----

    def config(self) -> dict[str, Any]:
        return {
            "profiles": [dict(p) for p in self.profiles],
            "active_profile": self.active_profile,
            "palette": list(self.palette),
            "programs": [r.program for r in self.runners.values()],
        }

    def status(self) -> dict[str, Any]:
        return {pid: r.status() for pid, r in self.runners.items()}

    def profile_name(self, profile_id: str) -> str:
        return next((p["name"] for p in self.profiles if p["id"] == profile_id), profile_id)

    def find_profile(self, key: str) -> str | None:
        """A profile id from an id or a name (case-insensitive)."""
        for p in self.profiles:
            if key == p["id"] or key.casefold() == p["name"].casefold():
                return p["id"]
        return None

    # ----- changes -----

    def _check_program(self, program: dict[str, Any]) -> dict[str, Any]:
        name = str(program.get("name", "")).strip()
        if not name:
            raise ValueError("a program needs a name")
        entities = list(dict.fromkeys(program.get("entities", [])))
        for entity in entities:
            if not isinstance(entity, str) or not entity.startswith(f"{CLIMATE_DOMAIN}."):
                raise ValueError(f"not a climate entity: {entity!r}")
            other = next(
                (r for r in self.runners.values() if r.id != program.get("id") and entity in r.entities), None
            )
            if other:
                raise ValueError(f"{entity} is already in program {other.program['name']!r}")
        parameters = self._check_parameters(program.get("parameters") or [])
        kinds = {p["id"]: PARAMETER_DOMAINS[p["entity"].split(".", 1)[0]] for p in parameters}
        ids = {p["id"] for p in self.profiles}
        profiles = {}
        for profile_id, week in (program.get("profiles") or {}).items():
            if profile_id not in ids:
                raise ValueError(f"unknown profile {profile_id!r}")
            week = schedule.validate_week(week)
            for point in (p for day in week.values() for p in day):
                params = {k: v for k, v in point.pop("params", {}).items() if k in kinds}  # deleted parameters: dropped
                for key, value in params.items():
                    if (kinds[key] == "toggle") != isinstance(value, bool):
                        raise ValueError(f"parameter {key!r} takes {'on / off' if kinds[key] == 'toggle' else 'a number'}")
                if params:
                    point["params"] = params
            profiles[profile_id] = week
        return {
            "id": program.get("id") or new_id(),
            "name": name,
            "entities": entities,
            "enabled": bool(program.get("enabled", True)),
            "hvac_mode": program.get("hvac_mode") or None,
            "self_off": bool(program.get("self_off", False)),
            "parameters": parameters,
            "profiles": profiles,
        }

    @staticmethod
    def _check_parameters(parameters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Slot parameters of a program: entities a slot may also set (switch / input_boolean, number / input_number)."""
        out: list[dict[str, Any]] = []
        for param in parameters:
            entity = param.get("entity")
            if not isinstance(entity, str) or entity.split(".", 1)[0] not in PARAMETER_DOMAINS or "." not in entity:
                raise ValueError(f"a parameter is a switch, input_boolean, number or input_number entity: {entity!r}")
            if any(p["entity"] == entity for p in out):
                raise ValueError(f"{entity} is twice in the parameters")
            param_id = param.get("id") or new_id()
            if any(p["id"] == param_id for p in out):
                raise ValueError(f"two parameters with the id {param_id!r}")
            out.append({
                "id": param_id,
                "entity": entity,
                "name": str(param.get("name") or "").strip() or None,
                "icon": str(param.get("icon") or "").strip() or None,
                "icon_off": str(param.get("icon_off") or "").strip() or None,
            })
        return out

    @callback
    def save_program(self, program: dict[str, Any]) -> dict[str, Any]:
        if program.get("id") and program["id"] not in self.runners:
            raise ValueError(f"unknown program {program['id']!r}")
        program = self._check_program(program)
        old = self.runners.get(program["id"])
        if old:
            old.stop()
            if old.program["name"] != program["name"]:
                if device := self._device(program["id"]):
                    dr.async_get(self.hass).async_update_device(device.id, name=program["name"])
        # Saving a program drops its overrides; what the engine turned off stays to turn back on.
        runner = ProgramRunner(self, program, {}, old.turned_off if old else set())
        self.runners[program["id"]] = runner
        self.save()
        if not old:
            async_dispatcher_send(self.hass, SIGNAL_PROGRAM_ADDED, program["id"])
        if self._started:
            runner.start()
        else:
            self.notify()
        return program

    @callback
    def set_enabled(self, program_id: str, enabled: bool) -> None:
        program = dict(self._runner(program_id).program, enabled=enabled)
        self.save_program(program)

    @callback
    def delete_program(self, program_id: str) -> None:
        runner = self.runners.pop(self._runner(program_id).id)
        runner.stop()
        registry = er.async_get(self.hass)
        for platform, key in PROGRAM_ENTITIES.items():
            if entity_id := registry.async_get_entity_id(platform, DOMAIN, f"{program_id}_{key}"):
                registry.async_remove(entity_id)
        if device := self._device(program_id):
            dr.async_get(self.hass).async_remove_device(device.id)
        self.save()
        self.notify()

    @callback
    def save_profiles(self, profiles: list[dict[str, Any]]) -> list[dict[str, str]]:
        known = {p["id"] for p in self.profiles}
        out: list[dict[str, str]] = []
        names: set[str] = set()
        for profile in profiles:
            name = str(profile.get("name", "")).strip()
            if not name:
                raise ValueError("a profile needs a name")
            if name.casefold() in names:
                raise ValueError(f"two profiles named {name!r}")
            names.add(name.casefold())
            pid = profile.get("id") or new_id()
            if profile.get("id") and pid not in known:
                raise ValueError(f"unknown profile {pid!r}")
            out.append({"id": pid, "name": name})
        if not any(p["id"] == DEFAULT_PROFILE for p in out):
            raise ValueError("the default profile cannot be deleted")
        out.sort(key=lambda p: p["id"] != DEFAULT_PROFILE)  # default first
        kept = {p["id"] for p in out}
        self.profiles = out
        changed_active = self.active_profile not in kept
        if changed_active:
            self.active_profile = DEFAULT_PROFILE
        for runner in self.runners.values():
            dropped = set(runner.program["profiles"]) - kept
            for pid in dropped:
                del runner.program["profiles"][pid]
            if changed_active or dropped:
                runner.clear_overrides()
                if self._started:
                    runner.evaluate()
        self.save()
        self.notify()
        return out

    @callback
    def save_palette(self, values: list[Any]) -> list[float]:
        """The temperatures offered first in the card (any other can be set on a slot)."""
        palette = sorted({float(schedule.validate_value(v)) for v in values if v != OFF})
        self.palette = palette
        self.save()
        self.notify()
        return palette

    @callback
    def set_active_profile(self, profile_id: str) -> None:
        if profile_id not in {p["id"] for p in self.profiles}:
            raise ValueError(f"unknown profile {profile_id!r}")
        if profile_id == self.active_profile:
            return
        self.active_profile = profile_id
        for runner in self.runners.values():
            runner.clear_overrides()
            if self._started:
                runner.evaluate()
        self.save()
        self.notify()

    @callback
    def clear_overrides(self, program_id: str | None = None, entities: list[str] | None = None) -> None:
        runners = [self._runner(program_id)] if program_id else list(self.runners.values())
        for runner in runners:
            if runner.clear_overrides(entities) and self._started:
                runner.evaluate()
        self.save()
        self.notify()

    def _device(self, program_id: str) -> dr.DeviceEntry | None:
        devices = dr.async_get(self.hass)
        if hasattr(devices, "async_get_device_by_identifier"):  # Home Assistant 2026.x
            return devices.async_get_device_by_identifier((DOMAIN, program_id), self.entry_id)
        return devices.async_get_device({(DOMAIN, program_id)})

    def _runner(self, program_id: str) -> ProgramRunner:
        if program_id not in self.runners:
            raise ValueError(f"unknown program {program_id!r}")
        return self.runners[program_id]
