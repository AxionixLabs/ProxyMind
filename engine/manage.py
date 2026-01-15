#  __  __
# |  \/  | __ _ _ __   __ _  __ _  ___
# | |\/| |/ _` | '_ \ / _` |/ _` |/ _ \
# | |  | | (_| | | | | (_| | (_| |  __/
# |_|  |_|\__,_|_| |_|\__,_|\__, |\___|
#                           |___/
#

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

    __device_list: list[Device] = []

    def __init__(self):
        self.__lock = asyncio.Lock()

    @staticmethod
    async def __connect_devices() -> list[Device]:
        resp = await Terminal.cmd_line(["adb", "devices"])

        if not resp or not (lines := [line.strip() for line in resp.splitlines() if line.strip()]):
            return []

        if "not found" in (low := resp.lower()) or low.startswith("adb:") or low.startswith("error"):
            return []

        device_list: list[Device] = [
            Device(parts[0]) for line in lines[1:]
            if len(parts := line.split()) >= 2
            if parts[1] == "device"
        ]

        await asyncio.gather(
            *(device.st_load_info() for device in device_list)
        )

        return device_list

    async def refresh(self, *, force: bool = False) -> list[Device]:
        async with self.__lock:
            if not shutil.which("adb"):
                raise RuntimeError(f"ADB not found in PATH")

            if force or not self.__device_list:
                self.__device_list = await self.__connect_devices()

            if not self.__device_list:
                raise RuntimeError("Device not connected ...")

            return list(self.__device_list)


if __name__ == '__main__':
    pass
