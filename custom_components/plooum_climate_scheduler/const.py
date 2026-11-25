"""Constants of Plooum Climate Scheduler."""

from __future__ import annotations

DOMAIN = "plooum_climate_scheduler"
ENTRY_TITLE = "Plooum Climate Scheduler"

STORAGE_KEY = DOMAIN
STORAGE_VERSION = 1

# Seconds after a command during which a thermostat's state is not compared yet with what was
# asked: some report a stale state first.
GRACE_SECONDS = 60

SIGNAL_PROGRAM_ADDED = f"{DOMAIN}_program_added"

ATTR_PROFILE = "profile"
SERVICE_SET_PROFILE = "set_profile"
SERVICE_CLEAR_OVERRIDE = "clear_override"
