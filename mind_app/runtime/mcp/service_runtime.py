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
from engine.upgrade import UpgradeProgress
from mind_app.assets import ensure_asset
from mind_core.design import Design
from .service_exec_env import fetch_service_exec_env

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind


@dataclass(frozen=True, slots=True)
class ServiceRuntimeSpec:
    """描述本地服务运行时的路径和启动命令。"""
    supports: str
    executable: str
    launch_command: list[str]
    path_entries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ServiceRuntimeContext:
    """描述服务运行时在当前入口下的准备参数。"""
    spec: ServiceRuntimeSpec
    platform: str
    packaged: bool
    env_symbol: str
    app_desc: str


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
    current = os.environ.get("PATH", "")
    existing = {
        os.path.normcase(os.path.normpath(item))
        for item in current.split(env_symbol)
        if item
    }
    missing = [
        entry for entry in spec.path_entries
        if os.path.normcase(os.path.normpath(entry)) not in existing
    ]
    if not missing:
        return None

    os.environ["PATH"] = env_symbol.join([*missing, current] if current else missing)


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
    packaged: bool,
    explicit_upgrade: bool,
    anim_manager: AsyncAnimManager,
    design: Design,
    progress: UpgradeProgress | None = None,
) -> bool:
    """复用入口升级流程确认运行时资产。"""
    return await ensure_asset(
        asset=spec.executable,
        supports=spec.supports,
        packaged=packaged,
        explicit_upgrade=explicit_upgrade,
        anim_manager=anim_manager,
        design=design,
        progress=progress,
    )


async def ensure_service_runtime_asset(
    context: ServiceRuntimeContext,
    *,
    explicit_upgrade: bool,
    anim_manager: AsyncAnimManager,
    design: Design
) -> bool:
    """确认当前服务运行时资产存在，必要时执行升级流程。"""
    return await ensure_runtime_asset(
        context.spec,
        packaged=context.packaged,
        explicit_upgrade=explicit_upgrade,
        anim_manager=anim_manager,
        design=design,
    )


def service_runtime_asset_missing(context: ServiceRuntimeContext) -> bool:
    """判断服务运行时资产是否缺失。"""
    return context.packaged and not Path(context.spec.executable).exists()


async def prepare_service_runtime(
    context: ServiceRuntimeContext,
    *,
    anim_manager: AsyncAnimManager,
    design: Design,
    progress: UpgradeProgress | None = None,
) -> bool:
    """准备服务运行时资产、环境变量和执行权限。"""
    await ensure_service_runtime_asset(
        context,
        explicit_upgrade=False,
        anim_manager=anim_manager,
        design=design,
        progress=progress,
    )

    if context.packaged:
        prepend_runtime_paths(context.spec, env_symbol=context.env_symbol)
        verify_runtime_paths(
            context.spec,
            packaged=True,
            app_desc=context.app_desc
        )
        await authorize_runtime_files(context.spec, platform=context.platform)

    return True


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


async def prepare_and_start_service_runtime(
    mind: "Mind",
    *,
    label: str = "Helix MCP",
    confirm_download: typing.Callable[
        [ServiceRuntimeContext],
        typing.Awaitable[bool],
    ] | None = None,
    progress: UpgradeProgress | None = None,
) -> bool:
    """确认下载授权后准备并启动服务运行时。"""
    context = mind.require_service_runtime_context()

    if service_runtime_asset_missing(context):
        if confirm_download is None or not await confirm_download(context):
            return False

    prepared = await prepare_service_runtime(
        context,
        anim_manager=mind.anim_manager,
        design=mind.design,
        progress=progress,
    )
    if not prepared:
        return False

    await start_service_runtime(mind, label=label)
    mind.link_service_mcp(await fetch_service_exec_env())
    return True


if __name__ == '__main__':
    pass
