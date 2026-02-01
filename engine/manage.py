#  __  __
# |  \/  | __ _ _ __   __ _  __ _  ___
# | |\/| |/ _` | '_ \ / _` |/ _` |/ _ \
# | |  | | (_| | | | | (_| | (_| |  __/
# |_|  |_|\__,_|_| |_|\__,_|\__, |\___|
#                           |___/
#

import sys
import json
import time
import httpx
import typing
import asyncio
import subprocess
from loguru import logger
from engine.tinker import MindError
from mindnova import const


class ServerManage(object):
    """ServerManage class."""

    def __init__(self, *, cmd: list[str], base_url: str, timeout: float = 0.6):
        self.cmd = cmd
        self.base_url = base_url.rstrip("/")
        self.__client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def probe_healthz(self) -> bool:
        headers = {"accept": "application/json"}

        try:
            resp = await self.__client.request("GET", "/healthz", headers=headers)

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
            logger.debug(f"[Healthz] net timeout: {type(e).__name__}: {e}")
            return False
        except httpx.ConnectError as e:
            logger.debug(f"[Healthz] net connect: {type(e).__name__}: {e}")
            return False
        except httpx.RemoteProtocolError as e:
            logger.debug(f"[Healthz] remote protocol error: {e}")
            return False
        except httpx.HTTPError as e:
            logger.debug(f"[Healthz] httpx error: {type(e).__name__}: {e}")
            return False

        if resp.status_code >= 400:
            logger.debug(f"[Healthz] bad status: {resp.status_code}")
            return False

        ct = (resp.headers.get("content-type") or "").lower()
        if "application/json" not in ct:
            logger.debug(f"[Healthz] unexpected content-type: {ct!r}")
            return False

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug(f"[Healthz] json decode failed: {type(e).__name__}: {e}")
            return False

        logger.debug(f"[Healthz] {data}")

        return bool(data.get("ok")) and (data.get("service") == "helix mcp")

    async def spawn(self) -> None:
        kwargs: dict[str, typing.Any] = {
            "stdin"  : asyncio.subprocess.DEVNULL,
            "stdout" : asyncio.subprocess.DEVNULL,
            "stderr" : asyncio.subprocess.DEVNULL,
        }

        if sys.platform.startswith("win"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True

        try:
            await asyncio.create_subprocess_exec(*self.cmd, **kwargs)
        except Exception as e:
            raise MindError(f"Spawn failed: {type(e).__name__}: {e}") from e

    async def ensure_running(self, *, wait_sec: float = 10.0, interval: float = 0.3) -> None:
        if await self.probe_healthz():
            return None

        await self.spawn()

        deadline = time.monotonic() + max(0.5, wait_sec)
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            if await self.probe_healthz():
                return logger.debug(
                    f"SYNC ▸ {const.APP_DESC} MCP neural core online."
                )

        raise MindError(f"MCP not ready (healthz timeout)")

    async def aclose(self) -> None:
        await self.__client.aclose()


if __name__ == '__main__':
    pass
