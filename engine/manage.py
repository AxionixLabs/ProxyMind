#  __  __
# |  \/  | __ _ _ __   __ _  __ _  ___
# | |\/| |/ _` | '_ \ / _` |/ _` |/ _ \
# | |  | | (_| | | | | (_| | (_| |  __/
# |_|  |_|\__,_|_| |_|\__,_|\__, |\___|
#                           |___/
#

import time
import shutil
import typing
import asyncio
from loguru import logger
from engine.device import Device
from engine.terminal import Terminal
from engine.tinker import MindError
from mindnova import const


class ServerManage(object):

    def __init__(self):
        self.transports: typing.Optional[asyncio.subprocess.Process] = None

    async def input_stream(self) -> None:
        async for line in self.transports.stdout:
            stream = line.decode(const.CHARSET, const.IGNORE).strip()
            logger.debug(" ".join(stream.split()))

    async def error_stream(self) -> None:
        async for line in self.transports.stderr:
            stream = line.decode(const.CHARSET, const.IGNORE).strip()
            logger.debug(" ".join(stream.split()))

    async def mcp_begin(self, cmd: list[str]) -> None:
        if self.transports and self.transports.returncode is None:
            return None

        self.transports = await Terminal.cmd_link(cmd)

        asyncio.create_task(self.input_stream())
        asyncio.create_task(self.error_stream())

        for _ in range(5):
            if self.transports is not None:
                return logger.info(
                    f"Ⓜ️ SYNC ▸ {const.APP_DESC} MCP neural core online."
                )
            await asyncio.sleep(1)

        raise MindError(f"Application startup failure")

    async def mcp_final(self) -> None:
        if not self.transports or self.transports.returncode is not None:
            return None

        logger.info(f"☣️ SYNC ▸ {const.APP_DESC} MCP neural core shutting down...")

        self.transports.terminate()
        try:
            await asyncio.wait_for(self.transports.wait(), timeout=5)
        except asyncio.TimeoutError:
            self.transports.kill()

        logger.info(f"♻️ SYNC ▸ {const.APP_DESC} MCP neural core offline.")


class DeviceManage(object):

    def __init__(self):
        self.lock = asyncio.Lock()
        self.device_list: list[Device] = []
        self.last_refresh_ts: float = 0.0

    @property
    def snapshot(self) -> list[Device]:
        return list(self.device_list)

    async def connect(self) -> list[Device]:
        if not shutil.which("adb"):
            raise RuntimeError("ADB not found in PATH")

        resp = await Terminal.cmd_line(["adb", "devices"])

        if not resp or not (lines := [line.strip() for line in resp.splitlines() if line.strip()]):
            raise RuntimeError("Device not connected ...")

        if "not found" in (low := resp.lower()) or low.startswith("adb:") or low.startswith("error"):
            raise RuntimeError(f"ADB error: {resp.strip()}")

        self.device_list = [
            Device(parts[0]) for line in lines[1:]
            if len(parts := line.split()) >= 2 and parts[1] == "device"
        ]
        self.last_refresh_ts = time.time()

        await asyncio.gather(
            *(device.st_load_info() for device in self.device_list)
        )

        return self.snapshot

    async def refresh(self) -> list[Device]:
        async with self.lock:
            return await self.connect()

    async def refresh_with_ttl(self, ttl_sec: float = 1.0) -> list[Device]:
        if self.device_list and (time.time() - self.last_refresh_ts) < ttl_sec:
            return self.snapshot

        return await self.refresh()


if __name__ == '__main__':
    pass
