# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import typing
import asyncio
from loguru import logger
from engine.animation import AsyncAnimManager
from engine.signals import SignalHandler
from engine.manage import ServerManage
from engine.errors import MindError
from engine.tinker import Active
from mind_core.parser import Parser
from mind_core.preference import Preferences
from mind_core.service_config import ServiceConfig
from mind_nova import const
from mind_nova.services import service_endpoints
from mind_app.presentation.models import (
    StyledBlock,
    TextSpan,
    TextStyle
)
from ..controller import Mind
from ..frontend.contracts import ApplicationView
from ..runtime.environment.exec_env import clear_exec_env_cache
from ..runtime.environment.shell_tools import route_shell_tools
from ..runtime.environment.workspace import fetch_runtime_workspace_root
from ..runtime.mcp.service_runtime import (
    ServiceRuntimeContext,
    ensure_service_runtime_asset,
    prepare_and_start_service_runtime,
    resolve_service_runtime
)
from ..paths import (
    ensure_mcp_servers_file,
    ensure_mind_home,
    mind_config_path,
    mind_reports_dir,
    process_env
)
from .dispatch import run_selected_mode
from .frontend import (
    resolve_cli_design,
    resolve_cli_frontend
)
from .selection import (
    OutputMode,
    output_mode_uses_animation,
    resolve_cli_output_mode
)


async def main(
    entry_file: typing.Optional[str] = None,
    handler: SignalHandler | None = None
) -> int:
    """执行命令入口并管理入口级动画。"""
    entry_anim_manager = AsyncAnimManager()

    try:
        return await _run_main(entry_file, entry_anim_manager, handler)
    finally:
        await await_cleanup(entry_anim_manager.stop())


async def await_cleanup(awaitable: typing.Awaitable[None]) -> None:
    """在取消态下等待清理任务执行完成。"""
    task = asyncio.ensure_future(awaitable)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def _emit_helix_skipped(mind: Mind) -> None:
    """输出 Helix 启动被跳过的状态。"""
    mind.frontend.application.emit(ApplicationView(
        type="helix.skipped",
        renderable=StyledBlock(
            plain_text="Helix · skipped",
            spans=(
                TextSpan(
                    "Helix ",
                    TextStyle(foreground="#AFC7D8", bold=True),
                ),
                TextSpan(
                    "· skipped",
                    TextStyle(foreground="#7F8C9A", dim=True),
                ),
            ),
        ),
    ))
    mind.frontend.application.emit(ApplicationView(type="spacer"))


async def _start_tui_external_mcp(mind: Mind) -> None:
    """在 TUI 进入交互循环前启动外部 MCP。"""
    from ..tui.features.mcp import (
        finish_mcp_activity,
        render_external_mcp_start_status,
    )

    try:
        await mind.start_external_mcp_runtime(defer_activity_stop=True)
    except asyncio.CancelledError:
        await mind.await_cleanup(finish_mcp_activity(mind, "start"))
        raise
    except MindError as error:
        await mind.await_cleanup(finish_mcp_activity(mind, "start"))
        render_external_mcp_start_status(mind, error=error)
        return None
    except Exception as error:
        await mind.await_cleanup(finish_mcp_activity(mind, "start"))
        render_external_mcp_start_status(mind, error=error)
        return None

    await finish_mcp_activity(mind, "start")
    render_external_mcp_start_status(mind)


async def _start_tui_service_runtime(mind: Mind) -> None:
    """在 TUI 后台准备 Helix 服务运行时。"""
    from ..tui.features.helix import (
        finish_helix_activity,
        link_helix_runtime,
        render_helix_link_failure,
        render_helix_link_result
    )

    try:
        linked = await link_helix_runtime(mind, download_confirmed=True)
    except asyncio.CancelledError:
        await mind.await_cleanup(finish_helix_activity(mind))
        raise
    except (MindError, Exception) as error:
        await mind.await_cleanup(finish_helix_activity(mind))
        render_helix_link_failure(mind, error)
        return None

    await finish_helix_activity(mind)
    render_helix_link_result(mind, linked)


async def _finalize_mind(
    mind: Mind,
    *,
    output_mode: OutputMode,
    completed: bool
) -> None:
    """关闭前端和运行时资源，并在完整 TUI 会话后打印退出摘要。"""
    try:
        await mind.frontend.runtime.close()
    finally:
        await mind.close_runtime_resources()

    if completed and output_mode == "tui":
        from ..tui.core.runtime import require_tui_runtime

        runtime = require_tui_runtime(mind.frontend.runtime)
        runtime.print_exit_summary()


async def _run_main(
    entry_file: typing.Optional[str],
    entry_anim_manager: AsyncAnimManager,
    handler: SignalHandler | None = None
) -> int:
    """执行入口主流程。"""
    Active.silent()

    # 解析命令行参数
    parser = Parser()

    cmd_lines   = parser.parse_cmd
    output_mode = resolve_cli_output_mode(cmd_lines)
    frontend    = resolve_cli_frontend(output_mode)
    design      = resolve_cli_design(frontend, output_mode)

    # Notes: ========== Start from here ==========
    if output_mode_uses_animation(output_mode):
        frontend.application.emit(ApplicationView(type="intro"))

    # 获取命令行参数
    wires = sys.argv[1:]

    # 获取当前操作系统平台和应用名称
    platform = sys.platform.strip().lower()
    software = os.path.basename(os.path.abspath(sys.argv[0])).strip().lower()

    sys_symbol = os.sep
    env_symbol = os.path.pathsep

    # 根据应用名称确定工作目录和配置目录
    if software == f"{const.APP_NAME}.exe":
        mind_work = os.path.dirname(os.path.abspath(sys.argv[0]))
        mind_feasible = os.path.dirname(mind_work)
    elif software == f"{const.APP_NAME}":
        mind_work = os.path.dirname(sys.executable)
        mind_feasible = os.path.dirname(mind_work)
    elif software == f"{const.APP_NAME}.py":
        mind_work = os.path.dirname(os.path.abspath(entry_file or __file__))
        mind_feasible = mind_work
    else:
        raise MindError(f"{const.APP_DESC} compatible with {const.APP_NAME} command")

    # Notes: ========== 路径初始化 ==========
    _ = mind_feasible
    turbo = os.path.join(mind_work, const.SCHEMATIC, const.SUPPORTS).format()

    home = ensure_mind_home()

    src_opera_place = str(home)
    src_total_place = str(mind_reports_dir())

    ensure_mcp_servers_file()

    # Notes: ========== 激活日志 ==========
    level = const.SHOW_LEVEL

    pref = Preferences(str(mind_config_path()))

    # Notes: ========== 工具路径 ==========
    if platform == "win32":
        supports = os.path.join(turbo, "windows").format()
    elif platform == "darwin":
        supports = os.path.join(turbo, "macos").format()
    else:
        raise MindError(f"{const.APP_DESC} is not supported on this platform: {platform}.")

    packaged = not software.endswith(".py")

    runtime_spec = resolve_service_runtime(
        platform=platform,
        supports=supports,
        level=level,
        packaged=packaged
    )
    service_runtime_context = ServiceRuntimeContext(
        spec=runtime_spec,
        platform=platform,
        packaged=packaged,
        env_symbol=env_symbol,
        app_desc=const.APP_DESC
    )

    route_shell_tools(supports)
    clear_exec_env_cache()

    # Notes: ========== 升级流程 ==========
    if cmd_lines.upgrade:
        await ensure_service_runtime_asset(
            service_runtime_context,
            explicit_upgrade=True,
            anim_manager=entry_anim_manager,
            design=design,
        )
        return 0

    logger.debug(f"{'=' * 15} 系统调试 {'=' * 15}")
    logger.debug(f"操作系统: {platform}")
    logger.debug(f"核心数量: {(power := os.cpu_count())}")
    logger.debug(f"应用名称: {software}")
    logger.debug(f"系统路径: {sys_symbol}")
    logger.debug(f"环境变量: {env_symbol}")
    logger.debug(f"日志等级: {level}")
    logger.debug(f"工具目录: {turbo}")
    logger.debug(f"{'=' * 15} 系统调试 {'=' * 15}\n")

    logger.debug(f"{'=' * 15} 环境变量 {'=' * 15}")
    for env in os.environ["PATH"].split(env_symbol):
        logger.debug(f"ENV: {env}")
    logger.debug(f"{'=' * 15} 环境变量 {'=' * 15}\n")

    logger.debug(f"{'=' * 15} 工具路径 {'=' * 15}")
    logger.debug(f"TLS: {runtime_spec.executable}")
    logger.debug(f"{'=' * 15} 工具路径 {'=' * 15}\n")

    keywords = {
        "src_opera_place" : src_opera_place,
        "src_total_place" : src_total_place,
        "pref"            : pref,
        "anim_manager"    : entry_anim_manager,
        "animate"         : output_mode_uses_animation(output_mode),
        "frontend"        : frontend,
        "design"          : design,
    }

    # remote = await global_config_task
    remote = {}

    server: ServerManage = ServerManage(
        runtime_spec.launch_command,
        env=process_env(),
    )

    mind = Mind(wires, level, power, remote, **keywords)

    mind.bind_runtime(asyncio.get_running_loop(), asyncio.current_task())
    mind.bind_server_manager(server)
    mind.bind_service_runtime_context(service_runtime_context)

    if handler is not None:
        handler.bind_delegate(mind.signal_processor)

    completed: bool = False

    try:
        if output_mode == "tui":
            from ..tui.session.state import preload_tui_prompt_context

            await preload_tui_prompt_context(mind)
        await mind.frontend.runtime.open()

        if cmd_lines.mcp and output_mode != "tui":
            helix_linked = await prepare_and_start_service_runtime(mind)
            if not helix_linked:
                _emit_helix_skipped(mind)

        await mind.start_config_service()

        runtime_workspace_root = await fetch_runtime_workspace_root()
        if runtime_workspace_root is not None:
            mind.set_history_workspace(runtime_workspace_root)

        pref_task = asyncio.create_task(
            pref.load_pref(),
            name="startup preference"
        )
        domain_task = asyncio.create_task(
            ServiceConfig().load_domain(),
            name="startup service domain"
        )
        startup_tasks = [pref_task, domain_task]

        try:
            if output_mode == "tui":
                await _start_tui_external_mcp(mind)
            else:
                await mind.start_external_mcp_runtime()
            await pref_task
            service_endpoints.configure(await domain_task)
        finally:
            for task in startup_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*startup_tasks, return_exceptions=True)

        if output_mode == "tui":
            from ..tui.core.runtime import require_tui_runtime
            from ..tui.features.helix import confirm_tui_service_runtime_startup

            runtime = require_tui_runtime(mind.frontend.runtime)

            start_helix = False
            if cmd_lines.mcp:
                start_helix = await confirm_tui_service_runtime_startup(mind)
                if not start_helix:
                    _emit_helix_skipped(mind)

            if start_helix:
                runtime.start_background_task(
                    _start_tui_service_runtime(mind),
                    name="mind tui service runtime startup",
                )

        await run_selected_mode(mind, cmd_lines)

        completed = True

        return mind.exit_code

    finally:
        await _finalize_mind(
            mind,
            output_mode=output_mode,
            completed=completed,
        )


if __name__ == '__main__':
    pass
