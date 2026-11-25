"""End-to-end scenario of the engine against the dev Home Assistant (dev/ha.sh start), through
the websocket API like the card. Prints PASS / FAIL per check, exits 1 on a failure.
Takes about 3 minutes (it waits for a live slot change and the override grace delay).

    ~/git/HA-Plooum-Cards/dev/.venv/bin/python dev/scenario.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import itertools
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import aiohttp

URL = os.environ.get("HA_URL", "http://127.0.0.1:8132")
TOKEN = (Path(__file__).parent / ".ha-token").read_text().strip()
TZ = ZoneInfo("Europe/Paris")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WS = "plooum_climate_scheduler/"
failures = 0


def check(name: str, ok: bool, detail: object = "") -> None:
    global failures
    failures += not ok
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" — {detail}"), flush=True)


class Client:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session
        self.ids = itertools.count(1)
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.events: list[dict] = []
        self.pending: dict[int, asyncio.Future] = {}

    async def connect(self) -> None:
        self.ws = await self.session.ws_connect(f"{URL}/api/websocket")
        await self.ws.receive_json()
        await self.ws.send_json({"type": "auth", "access_token": TOKEN})
        await self.ws.receive_json()
        asyncio.get_running_loop().create_task(self._read())

    async def _read(self) -> None:
        async for msg in self.ws:
            data = msg.json()
            if data["type"] == "event":
                self.events.append(data["event"])
            elif data["id"] in self.pending:
                self.pending.pop(data["id"]).set_result(data)

    async def call(self, type_: str, **kwargs) -> dict:
        mid = next(self.ids)
        self.pending[mid] = asyncio.get_running_loop().create_future()
        await self.ws.send_json({"id": mid, "type": WS + type_, **kwargs})
        return await asyncio.wait_for(self.pending[mid], 10)

    async def state(self, entity: str) -> dict | None:
        async with self.session.get(f"{URL}/api/states/{entity}", headers=auth()) as r:
            return await r.json() if r.status == 200 else None

    async def service(self, domain: str, service: str, data: dict) -> None:
        async with self.session.post(f"{URL}/api/services/{domain}/{service}", headers=auth(), json=data) as r:
            r.raise_for_status()


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def week(points: list[tuple[str, object]]) -> dict:
    return {d: [{"at": a, "value": v} for a, v in points] for d in DAYS}


async def wait_for(client: Client, entity: str, test, timeout: float = 10) -> dict:
    state = None
    for _ in range(int(timeout * 4)):
        state = await client.state(entity)
        if state and test(state):
            return state
        await asyncio.sleep(0.25)
    return state


def temp(value):
    return lambda s: s["state"] != "off" and s["attributes"].get("temperature") == value


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        c = Client(session)
        await c.connect()

        # Clean slate: drop the programs and profiles of an earlier run.
        snap = (await c.call("get"))["result"]
        for program in snap["config"]["programs"]:
            await c.call("program/delete", program_id=program["id"])
        await c.call("profiles/save", profiles=[{"id": "default", "name": "Default"}])
        await c.call("profile/activate", profile_id="default")
        for room in ("salon", "chambre_1", "chambre_2"):
            await c.service("climate", "set_hvac_mode", {"entity_id": f"climate.{room}", "hvac_mode": "heat"})
            await c.service("climate", "set_temperature", {"entity_id": f"climate.{room}", "temperature": 7})

        sub = await c.call("subscribe")
        check("subscribe", sub["success"], sub)

        r = await c.call("profiles/save", profiles=[{"id": "default", "name": "Default"}, {"name": "Vacances"}])
        profiles = r["result"]
        holidays = next(p["id"] for p in profiles if p["name"] == "Vacances")
        check("profiles saved, default first", [p["name"] for p in profiles] == ["Default", "Vacances"], profiles)

        # The living room changes in a minute (live transition), on today.
        now = datetime.now(TZ)
        change = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        if change.date() != now.date() or change.hour == 0 and change.minute == 0:
            print("Run it again after 00:01")
            sys.exit(2)
        hhmm = change.strftime("%H:%M")
        r = await c.call("program/save", program={
            "name": "Salon", "entities": ["climate.salon"],
            "profiles": {"default": week([("00:00", 17), (hhmm, 21)]), holidays: week([("00:00", 16)])},
        })
        check("salon saved", r["success"], r)
        salon = r["result"]["id"]
        r = await c.call("program/save", program={
            "name": "Chambres", "entities": ["climate.chambre_1", "climate.chambre_2"],
            "profiles": {"default": week([("00:00", 19.5)])},
        })
        check("chambres saved", r["success"], r)
        chambres = r["result"]["id"]

        r = await c.call("program/save", program={"name": "Dup", "entities": ["climate.salon"]})
        check("a thermostat in two programs is refused", not r["success"], r)
        r = await c.call("program/save", program={"name": "Bad", "profiles": {"default": {"mon": [{"at": "25:00", "value": 20}]}}})
        check("an invalid week is refused", not r["success"], r)

        s = await wait_for(c, "climate.salon", temp(17))
        check("salon gets 17 now", temp(17)(s), s)
        s = await wait_for(c, "climate.chambre_2", temp(19.5))
        check("chambre 2 gets 19.5", temp(19.5)(s), s)

        sensor = await c.state("sensor.salon_scheduled_temperature")
        check("salon sensor", sensor and sensor["state"] == "17.0"
              and sensor["attributes"]["next_change"] == change.isoformat()
              and sensor["attributes"]["next_value"] == 21, sensor)
        check("program switch on", (await c.state("switch.salon_schedule") or {}).get("state") == "on")
        check("profile select", (await c.state("select.climate_scheduler_profile") or {}).get("state") == "Default")

        # Holidays: the living room uses its own week, the bedrooms (no holidays week) their default.
        await c.service("select", "select_option", {"entity_id": "select.climate_scheduler_profile", "option": "Vacances"})
        s = await wait_for(c, "climate.salon", temp(16))
        check("holidays: salon 16", temp(16)(s), s)
        snap = (await c.call("get"))["result"]
        check("holidays: chambres fall back on default", snap["status"][chambres]["profile"] == "default"
              and snap["status"][salon]["profile"] == holidays, snap["status"])
        await c.service("plooum_climate_scheduler", "set_profile", {"profile": "default"})
        s = await wait_for(c, "climate.salon", temp(17))
        check("set_profile back to default: salon 17", temp(17)(s), s)

        # Live slot change.
        wait = (change - datetime.now(TZ)).total_seconds() + 3
        print(f"… waiting {wait:.0f} s for the {hhmm} change")
        await asyncio.sleep(max(wait, 0))
        s = await wait_for(c, "climate.salon", temp(21))
        check(f"salon gets 21 at {hhmm}", temp(21)(s), s)

        # Manual change on a bedroom: an override until the next change (none: constant week).
        await c.service("climate", "set_temperature", {"entity_id": "climate.chambre_1", "temperature": 23})
        print("… waiting the 60 s grace after the engine's last command")
        for _ in range(80):
            snap = (await c.call("get"))["result"]
            if "climate.chambre_1" in snap["status"][chambres]["overrides"]:
                break
            await asyncio.sleep(1)
        overrides = snap["status"][chambres]["overrides"]
        check("manual 23 on chambre 1 is an override", overrides.get("climate.chambre_1", {}).get("value") == 23, overrides)
        check("chambre 1 keeps 23", temp(23)(await c.state("climate.chambre_1")))
        await c.service("climate", "set_temperature", {"entity_id": "climate.chambre_2", "temperature": 22})
        await asyncio.sleep(1)
        overrides = (await c.call("get"))["result"]["status"][chambres]["overrides"]
        check("a change after the grace is an override at once", "climate.chambre_2" in overrides, overrides)
        await c.service("climate", "set_temperature", {"entity_id": "climate.chambre_2", "temperature": 19.5})
        await asyncio.sleep(1)
        overrides = (await c.call("get"))["result"]["status"][chambres]["overrides"]
        check("back to the schedule ends the override", "climate.chambre_2" not in overrides, overrides)
        r = await c.call("override/clear", program_id=chambres)
        s = await wait_for(c, "climate.chambre_1", temp(19.5))
        check("override/clear gives chambre 1 back its 19.5", r["success"] and temp(19.5)(s), s)

        # Off, then back on with a temperature.
        await c.call("program/save", program={**snap["config"]["programs"][1], "profiles": {"default": week([("00:00", "off")])}})
        s = await wait_for(c, "climate.chambre_1", lambda s: s["state"] == "off")
        check("off slot turns chambre 1 off", s["state"] == "off", s)
        sensor = await c.state("sensor.chambres_scheduled_temperature")
        check("off: sensor unknown, scheduled off", sensor["state"] == "unknown" and sensor["attributes"]["scheduled"] == "off", sensor)
        await c.call("program/save", program={**snap["config"]["programs"][1], "profiles": {"default": week([("00:00", 18)])}})
        s = await wait_for(c, "climate.chambre_1", temp(18))
        check("temperature slot turns it back on (heat, 18)", s["state"] == "heat" and temp(18)(s), s)

        # Disabled: hands off.
        await c.service("switch", "turn_off", {"entity_id": "switch.salon_schedule"})
        await c.service("climate", "set_temperature", {"entity_id": "climate.salon", "temperature": 12})
        await asyncio.sleep(1)
        snap = (await c.call("get"))["result"]
        check("disabled program leaves the thermostat alone", temp(12)(await c.state("climate.salon"))
              and snap["status"][salon]["value"] is None and not snap["status"][salon]["overrides"], snap["status"][salon])
        await c.service("switch", "turn_on", {"entity_id": "switch.salon_schedule"})
        s = await wait_for(c, "climate.salon", temp(21))
        check("enabled again: salon back to 21", temp(21)(s), s)

        # Deleting a profile drops its weeks.
        await c.call("profiles/save", profiles=[{"id": "default", "name": "Défaut"}])
        snap = (await c.call("get"))["result"]
        salon_program = next(p for p in snap["config"]["programs"] if p["id"] == salon)
        check("deleted profile's weeks removed, default renamed",
              list(salon_program["profiles"]) == ["default"] and snap["config"]["profiles"][0]["name"] == "Défaut", snap["config"])
        r = await c.call("profiles/save", profiles=[{"name": "Other"}])
        check("the default profile cannot be deleted", not r["success"], r)

        check("subscription events received", len(c.events) > 5, len(c.events))

        # Delete: entities gone.
        await c.call("program/delete", program_id=chambres)
        await asyncio.sleep(1)
        check("deleted program's entities removed", await c.state("switch.chambres_schedule") is None)

    print(f"\n{'OK' if not failures else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)


asyncio.run(main())
