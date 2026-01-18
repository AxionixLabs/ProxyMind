#  _   _       _       __  __
# | | | |_   _| |__   |  \/  | __ _ _ __   __ _  __ _  ___
# | |_| | | | | '_ \  | |\/| |/ _` | '_ \ / _` |/ _` |/ _ \
# |  _  | |_| | |_) | | |  | | (_| | | | | (_| | (_| |  __/
# |_| |_|\__,_|_.__/  |_|  |_|\__,_|_| |_|\__,_|\__, |\___|
#                                               |___/
#
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import shutil
import asyncio
from backend.mcp_hub.hub_device import Device
from engine.terminal import Terminal


class DeviceManage(object):
    """Device Manage class."""

    def __init__(self):
        self.lock = asyncio.Lock()
        self.device_list: list[Device] = []
        self.last_refresh_ts: float = 0.0

    @property
    def snapshot(self) -> list[Device]:
        if not self.device_list:
            raise RuntimeError("Device not connected")

        return list(self.device_list)

    async def connect(self) -> list[Device]:
        if not shutil.which("adb"):
            raise RuntimeError("ADB not found in PATH")

        resp = await Terminal.cmd_line(["adb", "devices"])

        if not resp or not (lines := [line.strip() for line in resp.splitlines() if line.strip()]):
            raise RuntimeError("Device not connected")

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

    async def refresh(self, ttl_sec: float = 1.0) -> list[Device]:
        if self.device_list and (time.time() - self.last_refresh_ts) < ttl_sec:
            return self.snapshot

        async with self.lock:
            return await self.connect()


if __name__ == '__main__':
    pass
