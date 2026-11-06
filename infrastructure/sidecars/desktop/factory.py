# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import platform as platform_info
import sys
import zipfile
from pathlib import Path

from agent.ports.desktop import (
    DesktopError,
    DesktopSessionPort,
)
from infrastructure.sidecars.desktop.session import create_desktop_session
from infrastructure.sidecars.desktop.install import DesktopRuntimeInstaller
from infrastructure.errors import AppError
from infrastructure.update.runtime import UpgradeProgress


class DesktopSidecarFactory:
    """首次获准使用时解析用户运行缓存，不在构造时下载或启动。"""

    def __init__(
        self, cache_root: Path, *, platform: str | None = None,
        architecture: str | None = None, progress: UpgradeProgress | None = None,
        system_version: str | None = None,
    ) -> None:
        """保存组合根解析的资产位置和平台选择。"""
        self._platform = sys.platform if platform is None else platform
        self._architecture = platform_info.machine() if architecture is None else architecture
        self._system_version = platform_info.mac_ver()[0] if system_version is None else system_version
        self._cache_root = cache_root
        self._progress = progress
        self._installer: DesktopRuntimeInstaller | None = None

    async def __call__(self) -> DesktopSessionPort:
        """在首次获准使用时检查资产并启动对应平台进程。"""
        machine = self._architecture.lower()
        if not ((self._platform == "win32" and machine in {"amd64", "x86_64", "x64"})
                or (self._platform == "darwin" and machine in {"arm64", "aarch64"})):
            raise DesktopError("unsupported_platform", f"Desktop automation is unavailable on {self._platform}/{self._architecture}; supported targets are Windows x64 and macOS ARM64")
        if self._platform == "darwin":
            parts = self._system_version.split(".")
            if not parts or not all(part.isdecimal() for part in parts):
                raise DesktopError("unsupported_platform", "Cannot determine the macOS version")
            version = tuple(int(part) for part in parts)
            if (version + (0, 0))[:2] < (14, 2):
                raise DesktopError("unsupported_platform", "Desktop automation requires macOS 14.2 or later")
        if self._installer is None:
            self._installer = DesktopRuntimeInstaller(self._cache_root, platform=self._platform, progress=self._progress)
        try:
            executable = await self._installer.ensure()
        except (OSError, ValueError, AppError, zipfile.BadZipFile, TimeoutError) as error:
            raise DesktopError("unavailable", f"CUA runtime installation failed: {error}") from error
        return await create_desktop_session(executable, platform=self._platform)


if __name__ == '__main__':
    pass
