"""Onboard the Climate Scheduler dev Home Assistant (first run), store a long-lived token in
dev/.ha-token, and set Plooum Climate Scheduler up. Idempotent.
Run by dev/ha.sh start, with the Home Assistant venv."""

import asyncio
import os
from pathlib import Path
import time

import aiohttp

URL = os.environ.get("HA_URL", "http://127.0.0.1:8132")
CLIENT_ID = URL + "/"
# Test credentials of this local dev instance only (bound to 127.0.0.1).
USERNAME, PASSWORD = "dev", "climate-scheduler-dev"
TOKEN_FILE = Path(__file__).parent / ".ha-token"


def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN_FILE.read_text().strip()}"}


async def onboard(s: aiohttp.ClientSession) -> None:
    async with s.get(f"{URL}/api/onboarding") as r:
        steps = {x["step"]: x["done"] for x in await r.json()}
    if not steps.get("user"):
        async with s.post(f"{URL}/api/onboarding/users", json={
            "client_id": CLIENT_ID, "name": "Dev", "username": USERNAME, "password": PASSWORD, "language": "en",
        }) as r:
            r.raise_for_status()
            code = (await r.json())["auth_code"]
    else:
        async with s.post(f"{URL}/auth/login_flow", json={
            "client_id": CLIENT_ID, "handler": ["homeassistant", None], "redirect_uri": CLIENT_ID,
        }) as r:
            flow = (await r.json())["flow_id"]
        async with s.post(f"{URL}/auth/login_flow/{flow}", json={
            "client_id": CLIENT_ID, "username": USERNAME, "password": PASSWORD,
        }) as r:
            code = (await r.json())["result"]
    async with s.post(f"{URL}/auth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": CLIENT_ID,
    }) as r:
        access = (await r.json())["access_token"]
    for step, payload in {"core_config": {}, "analytics": {}, "integration": {"client_id": CLIENT_ID, "redirect_uri": CLIENT_ID}}.items():
        if not steps.get(step, True):
            async with s.post(f"{URL}/api/onboarding/{step}", json=payload, headers={"Authorization": f"Bearer {access}"}) as r:
                r.raise_for_status()
    async with s.ws_connect(f"{URL}/api/websocket") as ws:
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": access})
        await ws.receive_json()
        await ws.send_json({"id": 1, "type": "auth/long_lived_access_token",
                            "client_name": f"climate-scheduler-dev-{int(time.time())}", "lifespan": 3650})
        result = await ws.receive_json()
    TOKEN_FILE.write_text(result["result"] + "\n")
    TOKEN_FILE.chmod(0o600)
    print("Onboarding done")


async def main() -> None:
    async with aiohttp.ClientSession() as s:
        valid = False
        if TOKEN_FILE.exists():
            async with s.get(f"{URL}/api/", headers=headers()) as r:
                valid = r.status == 200
        if not valid:
            await onboard(s)
        for _ in range(120):
            async with s.get(f"{URL}/api/config", headers=headers()) as r:
                if (await r.json())["state"] == "RUNNING":
                    break
            await asyncio.sleep(1)
        async with s.get(f"{URL}/api/config/config_entries/entry?domain=plooum_climate_scheduler", headers=headers()) as r:
            entries = await r.json() if r.status == 200 else []
        if any(e.get("domain") == "plooum_climate_scheduler" for e in entries):
            return
        async with s.post(f"{URL}/api/config/config_entries/flow", headers=headers(),
                          json={"handler": "plooum_climate_scheduler", "show_advanced_options": False}) as r:
            flow = await r.json()
        async with s.post(f"{URL}/api/config/config_entries/flow/{flow['flow_id']}", headers=headers(), json={}) as r:
            print("Plooum Climate Scheduler:", (await r.json()).get("type"))


asyncio.run(main())
