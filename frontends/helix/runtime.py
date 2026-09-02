# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from collections.abc import (
    Awaitable,
    Callable,
)
from agent.domain.tool_policy import ToolFilterMode
from agent.ports.frontend import FrontendActivityPort
from agent.ports.process_lifecycle import ProcessLifecyclePort
from infrastructure.services.helix_environment import fetch_service_exec_env
from infrastructure.services.server_manager import ServerManage
from infrastructure.update.assets import ensure_asset
from infrastructure.update.runtime import UpgradeProgress

from infrastructure.services.runtime_context import (
    ServiceRuntimeContext,
    ServiceRuntimeSpec,
)
from infrastructure.services.runtime_setup import (
    authorize_runtime_files,
    prepend_runtime_paths,
    service_runtime_asset_missing,
    verify_runtime_paths,
)
from observability import (
    observe,
    observe_exception,
)


class HelixServiceRuntimePort(typing.Protocol):
    """描述 Helix 启动编排使用的服务生命周期能力。

    实现方持有服务进程、启动互斥和保活资源；调用方不得绕过该端口创建或关闭
    进程所有者。
    """

    @property
    def manager(self) -> ServerManage | None:
        """返回已绑定的服务进程管理器。"""
        ...

    async def ensure_ready(self, *, wait_sec: float = 10.0) -> None:
        """确保服务在预算内进入就绪状态。"""
        ...

    def require_context(self) -> ServiceRuntimeContext:
        """返回已绑定的服务准备上下文。"""
        ...

    async def run_startup(
        self,
        operation: Callable[[], Awaitable[bool]],
    ) -> bool:
        """串行执行或复用当前服务准备任务。"""
        ...

    def start_keepalive(self) -> None:
        """启动服务保活任务。"""
        ...


class HelixToolLinkPort(typing.Protocol):
    """描述服务启动完成后链接工具会话所需的端口。"""

    def link_service(
        self,
        exec_env: dict[str, typing.Any] | None = None,
        *,
        tool_profile: ToolFilterMode = "app",
    ) -> None:
        """把已就绪服务链接到后续工具会话。"""
        ...


class HelixRuntimeHost(typing.Protocol):
    """描述 Helix 前台启动编排所需的最小宿主。

    实现方拥有服务运行时和前端活动状态；本模块只串行准备、展示并链接一次服务，
    不接管宿主或后台服务的最终关闭生命周期。
    """

    service_runtime: HelixServiceRuntimePort
    execution: HelixToolLinkPort
    activity: FrontendActivityPort
    lifecycle: ProcessLifecyclePort


async def ensure_runtime_asset(
    spec: ServiceRuntimeSpec,
    *,
    packaged: bool,
    explicit_upgrade: bool,
    progress: UpgradeProgress,
) -> bool:
    """复用入口升级流程确认运行时资产。"""
    return await ensure_asset(
        asset=spec.executable,
        supports=spec.supports,
        packaged=packaged,
        explicit_upgrade=explicit_upgrade,
        progress=progress,
    )


async def ensure_service_runtime_asset(
    context: ServiceRuntimeContext,
    *,
    explicit_upgrade: bool,
    progress: UpgradeProgress,
) -> bool:
    """确认当前服务运行时资产存在，必要时执行升级流程。"""
    return await ensure_runtime_asset(
        context.spec,
        packaged=context.packaged,
        explicit_upgrade=explicit_upgrade,
        progress=progress,
    )


async def prepare_service_runtime(
    context: ServiceRuntimeContext,
    *,
    progress: UpgradeProgress,
) -> bool:
    """准备服务运行时资产、环境变量和执行权限。"""
    await ensure_service_runtime_asset(
        context,
        explicit_upgrade=False,
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
    mind: HelixRuntimeHost,
    *,
    label: str = "Helix MCP",
    defer_activity_stop: bool = False
) -> None:
    """启动服务运行时并维护状态动画。"""
    status: dict[str, typing.Any] = {"state": "starting", "error": "", "label": label}

    started_at = time.perf_counter()

    observe("helix.start", label=label)

    await mind.activity.start_inbuild(lambda: dict(status))

    try:
        await mind.service_runtime.ensure_ready(wait_sec=10.0)
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
            await mind.lifecycle.await_cleanup(mind.activity.stop(
                "inbuild",
                settle=False,
            ))

    mind.service_runtime.start_keepalive()

    observe(
        "helix.start.complete",
        elapsed_ms=int((time.perf_counter() - started_at) * 1000),
    )


async def prepare_and_start_service_runtime(
    mind: HelixRuntimeHost,
    *,
    tool_profile: ToolFilterMode = "app",
    label: str = "Helix MCP",
    confirm_download: typing.Callable[
        [ServiceRuntimeContext],
        typing.Awaitable[bool],
    ] | None = None,
    progress: UpgradeProgress,
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
            progress=progress,
        )
        if not prepared:
            return False

        await start_service_runtime(
            mind,
            label=label,
            defer_activity_stop=defer_activity_stop,
        )
        mind.execution.link_service(
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
