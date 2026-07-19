# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from pathlib import Path
from engine.animation import AsyncAnimManager
from engine.upgrade import Upgrade
from mind_core.design import Design


class EntryUpgradeProgress(object):
    """通过入口级动画管理器展示升级进度。"""

    def __init__(self, anim_manager: AsyncAnimManager, design: Design) -> None:
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
    design: Design
) -> bool:
    """按入口场景确认所需资产存在，必要时触发升级流程。"""
    missing = packaged and not Path(asset).exists()

    if not explicit_upgrade and not missing:
        return False

    up: Upgrade = Upgrade()
    await up.upgrade_app(
        supports,
        progress=EntryUpgradeProgress(anim_manager, design),
    )

    return True


if __name__ == '__main__':
    pass
