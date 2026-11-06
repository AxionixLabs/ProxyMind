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
    ) -> None:
        """保存组合根解析的资产位置和平台选择。"""
        self._platform = sys.platform if platform is None else platform
        self._architecture = platform_info.machine() if architecture is None else architecture
        self._installer = DesktopRuntimeInstaller(cache_root, progress=progress)

    async def __call__(self) -> DesktopSessionPort:
        """在首次获准使用时检查资产并启动对应平台进程。"""
        if self._platform != "win32" or self._architecture.lower() not in {"amd64", "x86_64", "x64"}:
            raise DesktopError("unsupported_platform", f"Desktop automation is unavailable on {self._platform}")
        try:
            executable = await self._installer.ensure()
        except (OSError, ValueError, AppError, zipfile.BadZipFile, TimeoutError) as error:
            raise DesktopError("unavailable", f"CUA runtime installation failed: {error}") from error
        return await create_desktop_session(executable, platform=self._platform)


if __name__ == '__main__':
    pass
