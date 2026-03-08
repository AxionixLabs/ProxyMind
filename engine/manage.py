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
import platform
import subprocess
from loguru import logger
from mindcore.design import Design
from engine.tinker import MindError
from mindnova import (
    const, request
)


class ServerManage(object):
    """ServerManage class."""

    def __init__(self, cmd: list[str], timeout: float = 0.6):
        self.cmd = cmd
        self.base_url = const.BASE_URL.rstrip("/")
        self.__client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    @staticmethod
    def has_new(local: dict[str, typing.Any], remote: dict[str, typing.Any]) -> bool:

        def parse_version(text: str, width: int = 3) -> tuple[int, ...]:
            text = (text or "").strip().lower()
            if text.startswith("v"):
                text = text[1:]

            parts: list[int] = []
            for item in text.split("."):
                try:
                    parts.append(int(item))
                except ValueError:
                    parts.append(0)

            if len(parts) < width:
                parts.extend([0] * (width - len(parts)))

            return tuple(parts[:width])

        lv = parse_version(str(local.get("version") or "0"))
        rv = parse_version(str(remote.get("version") or "0"))
        return rv > lv

    async def check_update(self) -> None:
        if not (local := await self.probe_version()):
            return None

        station, arch = sys.platform, platform.machine()

        if not (remote := await request.fetch_manifest(station, arch)):
            return None

        if self.has_new(local, remote):
            Design.notify_update(local, remote)
        else:
            logger.debug(
                f"[Version] up to date: "
                f"local=v{local.get('version') or '-'} remote=v{remote.get('version') or '-'}"
            )

    async def probe_version(self) -> typing.Optional[dict[str, typing.Any]]:
        headers = {"accept": "application/json"}

        try:
            resp = await self.__client.request("GET", "/version", headers=headers)
            resp.raise_for_status()
            data = resp.json()

        except Exception as e:
            return logger.debug(f"[Version] probe failed: {type(e).__name__}: {e}")

        if not isinstance(data, dict) or not data.get("ok"):
            return None

        return data

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

    async def ensure_running(self, wait_sec: float = 10.0, interval: float = 0.3) -> None:
        if await self.probe_healthz():
            return None

        await self.spawn()

        deadline = time.monotonic() + max(0.5, wait_sec)
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            if await self.probe_healthz():
                logger.debug(
                    f"SYNC ▸ {const.APP_DESC} MCP neural core online."
                )
                return await self.check_update()

        raise MindError(f"MCP not ready (healthz timeout)")

    async def spawn(self) -> None:
        kwargs: dict[str, typing.Any] = {
            "stdin"  : asyncio.subprocess.DEVNULL,
            "stdout" : asyncio.subprocess.DEVNULL,
            "stderr" : asyncio.subprocess.DEVNULL
        }

        if sys.platform.startswith("win"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True

        try:
            await asyncio.create_subprocess_exec(*self.cmd, **kwargs)
        except Exception as e:
            raise MindError(f"Spawn failed: {type(e).__name__}: {e}") from e

    async def close(self) -> None:
        await self.__client.aclose()


if __name__ == '__main__':
    pass
