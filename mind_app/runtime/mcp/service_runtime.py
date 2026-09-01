# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from infrastructure.platform.animation import AsyncAnimManager
from infrastructure.update.assets import ensure_asset
from infrastructure.update.runtime import UpgradeProgress
from frontends.terminal.contracts import TerminalDesign
from frontends.terminal.download_renderer import TerminalDownloadProgress
from observability import (
    observe,
    observe_exception
)
from agent.domain.tool_policy import ToolFilterMode
from infrastructure.services.runtime_context import (
    ServiceRuntimeContext,
    ServiceRuntimeSpec,
)
from infrastructure.services.runtime_setup import (
    authorize_runtime_files,
    ensure_runtime_started,
    prepend_runtime_paths,
    service_runtime_asset_missing,
    verify_runtime_paths,
)
from .service_exec_env import fetch_service_exec_env

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind


async def ensure_runtime_asset(
    spec: ServiceRuntimeSpec,
    *,
    packaged: bool,
    explicit_upgrade: bool,
    anim_manager: AsyncAnimManager,
    design: TerminalDesign | None,
    progress: UpgradeProgress | None = None
) -> bool:
    """复用入口升级流程确认运行时资产。"""
    resolved_progress = progress
    if resolved_progress is None:
        if design is None:
            raise RuntimeError("terminal design is required without upgrade progress")
        resolved_progress = TerminalDownloadProgress(anim_manager, design)

    return await ensure_asset(
        asset=spec.executable,
        supports=spec.supports,
        packaged=packaged,
        explicit_upgrade=explicit_upgrade,
        progress=resolved_progress,
    )


async def ensure_service_runtime_asset(
    context: ServiceRuntimeContext,
    *,
    explicit_upgrade: bool,
    anim_manager: AsyncAnimManager,
    design: TerminalDesign | None,
    progress: UpgradeProgress | None = None
) -> bool:
    """确认当前服务运行时资产存在，必要时执行升级流程。"""
    return await ensure_runtime_asset(
        context.spec,
        packaged=context.packaged,
        explicit_upgrade=explicit_upgrade,
        anim_manager=anim_manager,
        design=design,
        progress=progress,
    )


async def prepare_service_runtime(
    context: ServiceRuntimeContext,
    *,
    anim_manager: AsyncAnimManager,
    design: TerminalDesign | None,
    progress: UpgradeProgress | None = None
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


async def start_service_runtime(
    mind: "Mind",
    *,
    label: str = "Helix MCP",
    defer_activity_stop: bool = False
) -> None:
    """启动服务运行时并维护状态动画。"""
    status: dict[str, typing.Any] = {"state": "starting", "error": "", "label": label}

    started_at = time.perf_counter()

    observe("helix.start", label=label)

    await mind.start_inbuild_startup_anim(lambda: dict(status))

    try:
        ensure_ready = getattr(mind.service_runtime, "ensure_ready", None)
        if callable(ensure_ready):
            await ensure_ready(wait_sec=10.0)
        else:
            await ensure_runtime_started(mind.service_runtime.manager)
        status["state"] = "ready"
    except Exception as error:
        status["state"] = "failed"
        status["error"] = str(error)
        observe_exception(
            "helix.start.failed",
            error,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise

    finally:
        if not defer_activity_stop:
            await mind.await_cleanup(mind.stop_anim("inbuild", settle=False))

    mind.service_runtime.start_keepalive()

    observe(
        "helix.start.complete",
        elapsed_ms=int((time.perf_counter() - started_at) * 1000),
    )


async def prepare_and_start_service_runtime(
    mind: "Mind",
    *,
    tool_profile: ToolFilterMode = "app",
    label: str = "Helix MCP",
    confirm_download: typing.Callable[
        [ServiceRuntimeContext],
        typing.Awaitable[bool],
    ] | None = None,
    progress: UpgradeProgress | None = None,
    download_confirmed: bool = False,
    defer_activity_stop: bool = False
) -> bool:
    """确认下载授权后准备并启动服务运行时。"""
    started_at = time.perf_counter()

    observe(
        "helix.prepare.start",
        download_confirmed=download_confirmed,
    )

    async def prepare() -> bool:
        """在串行边界内完成本地服务准备和发布。"""
        context = mind.service_runtime.require_context()

        if service_runtime_asset_missing(context) and not download_confirmed:
            if confirm_download is None or not await confirm_download(context):
                observe("helix.download.declined", level="WARNING")
                return False

        prepared = await prepare_service_runtime(
            context,
            anim_manager=mind.anim_manager,
            design=mind.design,
            progress=progress,
        )
        if not prepared:
            return False

        await start_service_runtime(
            mind,
            label=label,
            defer_activity_stop=defer_activity_stop,
        )
        mind.link_service_mcp(
            await fetch_service_exec_env(),
            tool_profile=tool_profile,
        )
        return True

    try:
        linked = await mind.service_runtime.run_startup(prepare)
    except asyncio.CancelledError:
        observe(
            "helix.prepare.interrupted",
            level="WARNING",
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise
    except BaseException as error:
        observe_exception(
            "helix.prepare.failed",
            error,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise

    observe(
        "helix.prepare.complete",
        linked=linked,
        elapsed_ms=int((time.perf_counter() - started_at) * 1000),
    )

    return linked


if __name__ == '__main__':
    pass
