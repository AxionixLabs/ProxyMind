# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import sys
import time
import shutil
import typing
import asyncio
from loguru import logger
from backend.mcp_hub.hub_device import Device
from backend.utilities.process import Flux


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

    def resolve(self, serial: str | None = None) -> Device:
        device_list = self.snapshot

        if serial:
            for device in device_list:
                if device.serial == serial:
                    return device
            serials = [device.serial for device in device_list]
            raise RuntimeError(f"Device not found: {serial}; available={serials}")

        if len(device_list) == 1:
            return device_list[0]

        serials = [device.serial for device in device_list]
        raise RuntimeError(f"Multiple devices connected; specify serial. available={serials}")

    async def resolve_fresh(self, serial: str | None = None, ttl_sec: float = 0.0) -> Device:
        """刷新设备列表后解析目标设备。"""
        await self.refresh(ttl_sec=ttl_sec)
        return self.resolve(serial)

    async def connect(self) -> list[Device]:
        if not shutil.which("adb"):
            raise RuntimeError("ADB not found in PATH")

        logger.debug("adb devices probing")
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

        logger.info(
            f"device refresh ok count={len(self.device_list)} "
            f"serials={[device.serial for device in self.device_list]}"
        )

        return self.snapshot

    async def refresh(self, ttl_sec: float = 1.0) -> list[Device]:
        if self.device_list and (time.time() - self.last_refresh_ts) < ttl_sec:
            logger.debug(
                f"device refresh cache-hit ttl_sec={ttl_sec} count={len(self.device_list)}"
            )
            return self.snapshot

        async with self.lock:
            return await self.connect()


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

    @staticmethod
    async def connect_k6() -> typing.Optional[str]:
        if not shutil.which(application := "k6"):
            navigator = "https://github.com/grafana/k6/releases"
            raise RuntimeError(f"Requires {application}. install it first, {navigator}.")

        return (await Flux.cmd_line([application, "--version"]) or "").strip()


if __name__ == '__main__':
    pass
