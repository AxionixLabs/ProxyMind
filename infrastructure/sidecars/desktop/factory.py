# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
from pathlib import Path

from agent.ports.desktop import (
    DesktopError,
    DesktopSessionPort,
)
from infrastructure.sidecars.desktop.session import create_desktop_session


class DesktopSidecarFactory:
    """从应用资产根选择唯一平台产物，不在构造时加载或启动原生能力。"""

    def __init__(self, asset_root: Path, *, platform: str | None = None) -> None:
        """保存组合根解析的资产位置和平台选择。"""
        self._root = asset_root
        self._platform = sys.platform if platform is None else platform

    async def __call__(self) -> DesktopSessionPort:
        """在首次获准使用时检查资产并启动对应平台进程。"""
        if self._platform == "win32":
            executable = self._root / "cua_windows" / "publish" / "desktop-sidecar.exe"
        elif self._platform == "darwin":
            executable = self._root / "cua_macos" / "publish" / "desktop-sidecar"
        else:
            raise DesktopError("unsupported_platform", f"Desktop automation is unavailable on {self._platform}")
        return await create_desktop_session(executable, platform=self._platform)


if __name__ == '__main__':
    pass
