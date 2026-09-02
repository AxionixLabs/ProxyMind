# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import os
import stat
import sys
from pathlib import Path

from infrastructure.errors import AppError
from infrastructure.platform.terminal import Terminal
from infrastructure.services.server_manager import ServerManage
from observability import observe
from .runtime_context import (
    ServiceRuntimeContext,
    ServiceRuntimeSpec,
)


def runtime_status(server_manager: ServerManage | None) -> str:
    """返回本地服务管理器的可读状态。"""
    if server_manager is None:
        return "unbound"
    return f"bound port={server_manager.port}"


def resolve_service_runtime(
    *,
    platform: str,
    supports: str,
    level: str,
    packaged: bool,
    application_root: str | None = None,
) -> ServiceRuntimeSpec:
    """根据平台和运行形态解析本地服务运行时。"""
    if platform == "win32":
        executable = os.path.join(supports, "helix.dist", "helix.exe")
    elif platform == "darwin":
        executable = os.path.join(
            supports,
            "helix.app",
            "Contents",
            "MacOS",
            "helix",
        )
    else:
        raise AppError(f"unsupported platform: {platform}")

    launch_command = (
        [executable, "--level", level]
        if packaged
        else [sys.executable, "-m", "backend.helix", "--level", level]
    )

    return ServiceRuntimeSpec(
        supports=supports,
        executable=executable,
        launch_command=launch_command,
        path_entries=(os.path.dirname(executable),),
        working_directory=(None if packaged else application_root),
    )


def prepend_runtime_paths(
    spec: ServiceRuntimeSpec,
    *,
    env_symbol: str,
) -> None:
    """把运行时依赖目录加入 PATH。"""
    current = os.environ.get("PATH", "")

    existing = {
        os.path.normcase(os.path.normpath(item))
        for item in current.split(env_symbol)
        if item
    }

    missing = [
        entry
        for entry in spec.path_entries
        if os.path.normcase(os.path.normpath(entry)) not in existing
    ]

    if not missing:
        return None

    os.environ["PATH"] = env_symbol.join(
        [*missing, current]
        if current
        else missing
    )


def verify_runtime_paths(
    spec: ServiceRuntimeSpec,
    *,
    packaged: bool,
    app_desc: str,
) -> None:
    """检查打包形态下的运行时文件是否可被发现。"""
    if not packaged:
        return None

    target_name = os.path.basename(spec.executable)
    if not Path(spec.executable).is_file():
        raise AppError(f"{app_desc} missing files {target_name}")


async def authorize_runtime_files(
    spec: ServiceRuntimeSpec,
    *,
    platform: str,
) -> None:
    """在需要时补齐运行时文件执行权限。"""
    if platform != "darwin":
        return None

    targets = [
        item
        for item in [spec.executable]
        if Path(item).exists()
    ]
    pending = [
        item
        for item in targets
        if not (Path(item).stat().st_mode & stat.S_IXUSR)
    ]

    if not pending:
        return None

    for item in pending:
        observe("helix.authorize.start", path=item)

    for result in await asyncio.gather(
        *(Terminal.cmd_line(["chmod", "+x", item]) for item in pending),
        return_exceptions=True,
    ):
        observe("helix.authorize.complete", result=result)


def service_runtime_asset_missing(context: ServiceRuntimeContext) -> bool:
    """判断服务运行时资产是否缺失。"""
    return context.packaged and not Path(context.spec.executable).exists()


async def ensure_runtime_started(server_manager: ServerManage | None) -> None:
    """确认本地服务已经启动并可用。"""
    if server_manager is None:
        raise AppError("Runtime manager is not bound")
    await server_manager.ensure_running()


if __name__ == '__main__':
    pass
