# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import sys
import time
import shutil
import typing
import asyncio
from backend.mcp_hub.hub_device import Device
from backend.models.model_device import SemanticResult
from backend.utilities.flux import Flux


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

        resp = await Flux.cmd_line(["adb", "devices"])

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
            *(device.refresh_device_props() for device in self.device_list)
        )

        return self.snapshot

    async def refresh(self, ttl_sec: float = 1.0) -> list[Device]:
        if self.device_list and (time.time() - self.last_refresh_ts) < ttl_sec:
            return self.snapshot

        async with self.lock:
            return await self.connect()

    async def refresh_summary(self, ttl_sec: float = 1.0) -> dict[str, typing.Any]:
        device_list = await self.refresh(ttl_sec)
        preview = [(await device.device_snapshot()).get("text", "") for device in device_list]
        serials = [device.serial for device in device_list]

        return SemanticResult.from_text(
            f"refresh ok: devices={len(serials)}\n" + "\n".join(preview),
            data={
                "ok"      : True,
                "reason"  : None,
                "ttl_sec" : ttl_sec,
                "count"   : len(serials),
                "serials" : serials
            }
        ).to_dict()


class Requires(object):
    """Requires class."""

    @staticmethod
    async def connect_scrcpy() -> typing.Optional[str]:
        if not shutil.which(application := "scrcpy"):
            navigator = "https://github.com/Genymobile/scrcpy"
            raise RuntimeError(f"Requires {application}. install it first, {navigator}.")

        return (await Flux.cmd_line([application, "--version"]) or "").strip()

    @staticmethod
    async def connect_ffmpeg() -> typing.Optional[str]:
        if not shutil.which(application := "ffmpeg"):
            navigator = "https://www.ffmpeg.org/"
            raise RuntimeError(f"Requires {application}. install it first, {navigator}.")

        return (await Flux.cmd_line([application, "-version"]) or "").strip()

    @staticmethod
    async def connect_framix() -> typing.Optional[str]:
        if not shutil.which(application := "framix"):
            domain = "https://github.com/PlaxtonFlarion/SoftwareCenter/releases/tag"
            if sys.platform.startswith("win"):
                navigator = f"{domain}/Framix-windows-v1.0.0"
            else:
                navigator = f"{domain}/Framix-macos-v1.0.0"
            raise RuntimeError(f"Requires {application}. install it first, {navigator}.")

        return (await Flux.cmd_line([application, "-h"]) or "").strip()

    @staticmethod
    async def connect_memrix() -> typing.Optional[str]:
        if not shutil.which(application := "memrix"):
            domain = "https://github.com/PlaxtonFlarion/SoftwareCenter/releases/tag"
            if sys.platform.startswith("win"):
                navigator = f"{domain}/Memrix-windows-v1.0.0"
            else:
                navigator = f"{domain}/Memrix-macos-v1.0.0"
            raise RuntimeError(f"Requires {application}. install it first, {navigator}.")

        return (await Flux.cmd_line([application, "-h"]) or "").strip()


if __name__ == '__main__':
    pass
