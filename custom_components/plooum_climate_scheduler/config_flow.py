"""Config flow: a single entry without settings (programs and profiles are made in the card)."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN, ENTRY_TITLE


class ClimateSchedulerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=ENTRY_TITLE, data={})
        return self.async_show_form(step_id="user")
