#  __  __
# |  \/  | __ _ _ __   __ _  __ _  ___
# | |\/| |/ _` | '_ \ / _` |/ _` |/ _ \
# | |  | | (_| | | | | (_| | (_| |  __/
# |_|  |_|\__,_|_| |_|\__,_|\__, |\___|
#                           |___/
#

import sys
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
        headers = {
            "accept": "application/json"
        }
        try:
            response = await self.__client.request("GET", "/healthz", headers=headers)
            logger.debug(f"Healthz: {response.json()}")
            if response.status_code >= 400:
                return False

            ct = (response.headers.get("content-type") or "").lower()

            data = response.json() if "application/json" in ct else {}

            return bool(data.get("ok")) and (data.get("service") == "helix mcp")
        except Exception as e:
            _ = e
            return False

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

    async def ensure_running(self, *, wait_sec: float = 6.0, interval: float = 0.3) -> None:
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
