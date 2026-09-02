# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from pathlib import Path
from infrastructure.update.runtime import (
    Upgrade,
    UpgradeProgress,
)


async def ensure_asset(
    *,
    asset: str,
    supports: str,
    packaged: bool,
    explicit_upgrade: bool,
    progress: UpgradeProgress | None = None
) -> bool:
    """按入口场景确认所需资产存在，必要时触发升级流程。"""
    missing = packaged and not Path(asset).exists()

    if not explicit_upgrade and not missing:
        return False

    up: Upgrade = Upgrade()

    await up.upgrade_app(
        supports,
        progress=progress,
    )

    return True


if __name__ == '__main__':
    pass
