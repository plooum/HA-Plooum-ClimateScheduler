# Plooum Climate Scheduler

*[Version française](README.fr.md)*

Week schedules for the thermostats (`climate` entities) of Home Assistant, with **profiles** shared by the whole house (Default, Holidays…).

- A **program** drives one or more thermostats. For each profile it holds a week: per day, the times at which a temperature (or *off*) starts. A program without a week for the active profile uses its Default week.
- The active **profile** is a `select` entity: switch the whole house to *Holidays* from a dashboard or an automation.
- A thermostat changed by hand keeps its setting **until the next change** of its schedule, then the schedule takes over again.

Programs and profiles are edited in the **climate schedule card** of [HA-Plooum-Cards](https://github.com/plooum/HA-Plooum-Cards) (coming soon).

## Installation

1. HACS → Integrations → ⋮ → Custom repositories → `https://github.com/plooum/HA-Plooum-ClimateScheduler`, category *Integration*. Install **Plooum Climate Scheduler** and restart Home Assistant.
2. Settings → Devices & services → Add integration → **Plooum Climate Scheduler**. There is nothing to set.

## Entities

| Entity | Role |
| :--- | :--- |
| `select.climate_scheduler_profile` | the active profile of the house |
| `switch.<program>_schedule` | turns a program on or off; off, it leaves its thermostats alone |
| `sensor.<program>_scheduled_temperature` | the temperature of the current slot (empty when the slot is *off*). Attributes: `scheduled`, `profile` (the week in use), `next_change`, `next_value`, `overrides` (thermostats changed by hand, with their value and until when), `climate_entities` |

## Actions

| Action | Data |
| :--- | :--- |
| `plooum_climate_scheduler.set_profile` | `profile`: name of the profile |
| `plooum_climate_scheduler.clear_override` | `entity_id` (optional): thermostats changed by hand to give back to their schedule now; none: all |

```yaml
# Holidays while everybody is away
action: plooum_climate_scheduler.set_profile
data:
  profile: Holidays
```

## How it behaves

- The temperature of the current slot is applied when a slot starts, when Home Assistant starts, when a program is saved or turned on, and when the profile changes. Past slots are not replayed.
- A slot set to *off* turns the thermostats off (`hvac_mode: off`); the next temperature slot turns them back on (in their previous mode, else `heat`).
- Temperatures are kept between the thermostat's minimum and maximum.
- A thermostat that is unavailable gets the current slot as soon as it comes back.
- **Thermostats that turn themselves off** (a pellet stove's eco mode, a standby): tick *The thermostat turns itself off* in the program's settings. The scheduler then only turns back on what it turned off itself (an Off slot); a thermostat that went off on its own just gets its new target at the next change and relights by itself, and going off is not a manual change.
- A manual change is the target temperature, or turning the thermostat off or on; changing its mode (heat → auto) is not one. Changes reported in the 60 seconds after the scheduler's own command are only compared at the end of that delay (some thermostats first report a stale state).
- Manual changes survive a restart. Switching profile, saving or turning a program on, and `clear_override` end them.
- Times follow Home Assistant's time zone. On the night clocks go forward, a change set inside the skipped hour (02:30) happens one hour later.

## Developers

The card talks to the engine through the websocket API described in [docs/CONCEPTION.md](docs/CONCEPTION.md) (French). Tests: `python -m unittest discover tests`; a development Home Assistant with three fake thermostats: `dev/ha.sh start`, end-to-end scenario: `dev/scenario.py`.
