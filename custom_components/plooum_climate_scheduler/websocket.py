"""Websocket API of the schedule card: read, subscribe to and change profiles and programs."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN

PREFIX = f"{DOMAIN}/"


@callback
def async_register(hass: HomeAssistant) -> None:
    for command in (ws_get, ws_subscribe, ws_save_program, ws_delete_program, ws_save_profiles,
                    ws_activate_profile, ws_clear_override, ws_save_palette):
        websocket_api.async_register_command(hass, command)


def _scheduler(hass: HomeAssistant):
    from . import get_scheduler  # noqa: PLC0415 (circular)

    return get_scheduler(hass)


def _snapshot(hass: HomeAssistant) -> dict[str, Any]:
    scheduler = _scheduler(hass)
    return {"config": scheduler.config(), "status": scheduler.status()}


def _run(connection: websocket_api.ActiveConnection, msg: dict[str, Any], action) -> None:
    try:
        result = action()
    except ValueError as err:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, str(err))
    except HomeAssistantError as err:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, str(err))
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): PREFIX + "get"})
@callback
def ws_get(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    _run(connection, msg, lambda: _snapshot(hass))


@websocket_api.websocket_command({vol.Required("type"): PREFIX + "subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    try:
        scheduler = _scheduler(hass)
    except HomeAssistantError as err:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, str(err))
        return

    @callback
    def forward() -> None:
        connection.send_message(websocket_api.event_message(msg["id"], _snapshot(hass)))

    connection.subscriptions[msg["id"]] = scheduler.async_add_listener(forward)
    connection.send_result(msg["id"])
    forward()


_POINT = vol.Schema({vol.Required("at"): str, vol.Required("value"): vol.Any(vol.Coerce(float), "off")})
_PROGRAM = vol.Schema(
    {
        vol.Optional("id"): vol.Any(None, str),
        vol.Required("name"): str,
        vol.Optional("entities", default=list): [str],
        vol.Optional("enabled", default=True): bool,
        vol.Optional("hvac_mode"): vol.Any(None, str),
        vol.Optional("profiles", default=dict): {str: {str: [_POINT]}},
    }
)


@websocket_api.websocket_command({vol.Required("type"): PREFIX + "program/save", vol.Required("program"): _PROGRAM})
@callback
def ws_save_program(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    _run(connection, msg, lambda: _scheduler(hass).save_program(msg["program"]))


@websocket_api.websocket_command({vol.Required("type"): PREFIX + "program/delete", vol.Required("program_id"): str})
@callback
def ws_delete_program(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    _run(connection, msg, lambda: _scheduler(hass).delete_program(msg["program_id"]))


@websocket_api.websocket_command(
    {
        vol.Required("type"): PREFIX + "profiles/save",
        vol.Required("profiles"): [{vol.Optional("id"): vol.Any(None, str), vol.Required("name"): str}],
    }
)
@callback
def ws_save_profiles(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    _run(connection, msg, lambda: _scheduler(hass).save_profiles(msg["profiles"]))


@websocket_api.websocket_command({vol.Required("type"): PREFIX + "profile/activate", vol.Required("profile_id"): str})
@callback
def ws_activate_profile(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    _run(connection, msg, lambda: _scheduler(hass).set_active_profile(msg["profile_id"]))


@websocket_api.websocket_command(
    {
        vol.Required("type"): PREFIX + "override/clear",
        vol.Optional("program_id"): str,
        vol.Optional("entity_id"): [str],
    }
)
@callback
def ws_clear_override(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    _run(connection, msg, lambda: _scheduler(hass).clear_overrides(msg.get("program_id"), msg.get("entity_id")))


@websocket_api.websocket_command(
    {vol.Required("type"): PREFIX + "palette/save", vol.Required("palette"): [vol.Coerce(float)]}
)
@callback
def ws_save_palette(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    _run(connection, msg, lambda: _scheduler(hass).save_palette(msg["palette"]))
