#  __  __
# |  \/  | __ _ _ __   __ _  __ _  ___
# | |\/| |/ _` | '_ \ / _` |/ _` |/ _ \
# | |  | | (_| | | | | (_| | (_| |  __/
# |_|  |_|\__,_|_| |_|\__,_|\__, |\___|
#                           |___/
#

import shutil
import asyncio
from .device import Device
from engine.terminal import Terminal


class DeviceManage(object):

    __device_list: list[Device] = []

    def __init__(self):
        self.__lock = asyncio.Lock()

    @staticmethod
    async def __devices() -> list[Device]:
        resp = await Terminal.cmd_line(["adb", "devices"])

        if not resp or not (lines := [line.strip() for line in resp.splitlines() if line.strip()]):
            return []

        if "not found" in (low := resp.lower()) or low.startswith("adb:") or low.startswith("error"):
            return []

        device_list: list[Device] = [
            Device(parts[0]) for line in lines[1:] if len(parts := line.split()) >= 2 if parts[1] == "device"
        ]
        await asyncio.gather(
            *(device.load_info() for device in device_list)
        )

        return device_list

    async def refresh(self, *, force: bool = False) -> list[Device]:
        async with self.__lock:
            if not shutil.which("adb"):
                raise RuntimeError(f"ADB not found in PATH")

            if force or not self.__device_list:
                self.__device_list = await self.__devices()

            if not self.__device_list:
                raise RuntimeError("Device not connected ...")
            return list(self.__device_list)


if __name__ == '__main__':
    pass
