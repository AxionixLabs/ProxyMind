# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import stat
import typing
import asyncio
from pathlib import Path
from dataclasses import dataclass
from loguru import logger
from engine.animation import AsyncAnimManager
from engine.manage import ServerManage
from engine.terminal import Terminal
from engine.tinker import MindError
from mind_app.assets import ensure_asset

if typing.TYPE_CHECKING:
    from mind_app.mind_core import Mind


@dataclass(frozen=True, slots=True)
class ServiceRuntimeSpec:
    """描述本地服务运行时的路径和启动命令。"""
    supports: str
    executable: str
    launch_command: list[str]
    path_entries: tuple[str, ...]


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
    packaged: bool
) -> ServiceRuntimeSpec:
    """根据平台和运行形态解析本地服务运行时。"""
    if platform == "win32":
        executable = os.path.join(supports, "helix.dist", "helix.exe")
    elif platform == "darwin":
        executable = os.path.join(supports, "helix.app", "Contents", "MacOS", "helix")
    else:
        raise MindError(f"unsupported platform: {platform}")

    launch_command = [executable, "--level", level] if packaged else [
        sys.executable, "-m", "backend.helix", "--level", level
    ]

    return ServiceRuntimeSpec(
        supports=supports,
        executable=executable,
        launch_command=launch_command,
        path_entries=(os.path.dirname(executable),)
    )


def prepend_runtime_paths(
    spec: ServiceRuntimeSpec,
    *,
    env_symbol: str
) -> None:
    """把运行时依赖目录加入 PATH。"""
    for entry in spec.path_entries:
        os.environ["PATH"] = entry + env_symbol + os.environ.get("PATH", "")


def verify_runtime_paths(
    spec: ServiceRuntimeSpec,
    *,
    packaged: bool,
    app_desc: str
) -> None:
    """检查打包形态下的运行时文件是否可被发现。"""
    if not packaged:
        return None

    target_name = os.path.basename(spec.executable)
    if not Path(spec.executable).is_file():
        raise MindError(f"{app_desc} missing files {target_name}")


async def authorize_runtime_files(
    spec: ServiceRuntimeSpec,
    *,
    platform: str
) -> None:
    """在需要时补齐运行时文件执行权限。"""
    if platform != "darwin":
        return None

    targets = [
        item for item in [spec.executable] if Path(item).exists()
    ]
    pending = [
        item for item in targets if not (Path(item).stat().st_mode & stat.S_IXUSR)
    ]

    if not pending:
        return None

    for item in pending:
        logger.debug(f"Authorizing: {item}")

    for result in await asyncio.gather(
        *(Terminal.cmd_line(["chmod", "+x", item]) for item in pending),
        return_exceptions=True
    ):
        logger.debug(f"Authorize: {result}")


async def ensure_runtime_asset(
    spec: ServiceRuntimeSpec,
    *,
    software: str,
    explicit_upgrade: bool,
    anim_manager: AsyncAnimManager
) -> bool:
    """复用入口升级流程确认运行时资产。"""
    return await ensure_asset(
        asset=spec.executable,
        supports=spec.supports,
        software=software,
        explicit_upgrade=explicit_upgrade,
        anim_manager=anim_manager
    )


async def ensure_runtime_started(server_manager: ServerManage | None) -> None:
    """确认本地服务已经启动并可用。"""
    if server_manager is None:
        raise MindError("Runtime manager is not bound")
    await server_manager.ensure_running()


async def start_service_runtime(
    mind: "Mind",
    *,
    label: str = "Helix MCP"
) -> None:
    """启动服务运行时并维护状态动画。"""
    status: dict[str, typing.Any] = {"state": "starting", "error": "", "label": label}
    await mind.start_inbuild_startup_anim(lambda: dict(status))
    try:
        await ensure_runtime_started(mind.server_manager)
        status["state"] = "ready"
    except Exception as error:
        status["state"] = "failed"
        status["error"] = str(error)
        raise
    finally:
        await mind.await_cleanup(mind.stop_anim())

    mind.start_keepalive_supervisor()


if __name__ == '__main__':
    pass
