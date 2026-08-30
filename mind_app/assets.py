# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from pathlib import Path
from infrastructure.platform.animation import AsyncAnimManager
from infrastructure.update.runtime import (
    Upgrade,
    UpgradeProgress
)
from mind_app.runtime.design import TerminalDesign


class EntryUpgradeProgress(object):
    """通过入口级动画管理器展示升级进度。"""

    def __init__(self, anim_manager: AsyncAnimManager, design: TerminalDesign) -> None:
        """绑定入口级动画管理器。"""
        self.anim_manager = anim_manager
        self.design       = design

    async def start(self, state: dict) -> None:
        """启动入口升级动画。"""
        await self.anim_manager.start(
            lambda stop_event: self.design.download_animation(state, stop_event)
        )

    async def stop(self) -> None:
        """停止入口升级动画。"""
        await self.anim_manager.stop()


async def ensure_asset(
    *,
    asset: str,
    supports: str,
    packaged: bool,
    explicit_upgrade: bool,
    anim_manager: AsyncAnimManager,
    design: TerminalDesign | None,
    progress: UpgradeProgress | None = None
) -> bool:
    """按入口场景确认所需资产存在，必要时触发升级流程。"""
    missing = packaged and not Path(asset).exists()

    if not explicit_upgrade and not missing:
        return False

    resolved_progress = progress
    if resolved_progress is None:
        if design is None:
            raise RuntimeError("terminal design is required without upgrade progress")
        resolved_progress = EntryUpgradeProgress(anim_manager, design)

    up: Upgrade = Upgrade()

    await up.upgrade_app(
        supports,
        progress=resolved_progress,
    )

    return True


if __name__ == '__main__':
    pass
