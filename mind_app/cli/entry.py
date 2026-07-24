# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
import asyncio
from loguru import logger
from engine.animation import AsyncAnimManager
from engine.signals import SignalHandler
from engine.manage import ServerManage
from engine.errors import MindError
from mind_core.preference import Preferences
from mind_core.application_paths import resolve_application_layout
from mind_core.service_config import ServiceConfig
from mind_nova import const
from mind_nova.services import service_endpoints
from engine.observability import (
    observe,
    observe_exception
)
from ..reporting import RunReport
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
    mind_home,
    mind_mcp_servers_path,
    mind_reports_dir,
    process_env
)
from .dispatch import run_selected_mode
from .commands import (
    DoctorCommand,
    HelixUpgradeCommand,
    McpServerCommand,
    command_uses_helix,
)
from .doctor import (
    DoctorContext,
    diagnose,
    render_doctor_report,
)
from .frontend import (
    resolve_cli_design,
    resolve_cli_frontend
)
from .selection import (
    OutputMode,
    output_mode_uses_animation,
    resolve_cli_output_mode
)
from .parser import parse_cli_command


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
    observe(
        "app.shutdown.start",
        output_mode=output_mode,
        completed=completed,
        exit_code=mind.exit_code,
    )
    try:
        await mind.frontend.runtime.close()
    except BaseException as error:
        observe_exception("frontend.close.failed", error)
        raise
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
    logger.remove()

    command = parse_cli_command()
    if isinstance(command, McpServerCommand):
        from mind_app.mcp.server import run_mind_mcp_server

        return await run_mind_mcp_server(entry_file=entry_file)

    output_mode = resolve_cli_output_mode(command)
    frontend    = resolve_cli_frontend(output_mode)
    design      = resolve_cli_design(frontend, output_mode)

    # Notes: ========== Start from here ==========
    if output_mode_uses_animation(output_mode):
        frontend.application.emit(ApplicationView(type="intro"))

    # 获取当前入口对应的源码或打包资源布局
    try:
        app_layout = resolve_application_layout(entry_file=entry_file)
    except ValueError as error:
        raise MindError(
            f"{const.APP_DESC} compatible with {const.APP_NAME} command"
        ) from error

    platform = app_layout.platform
    env_symbol = os.path.pathsep
    supports = str(app_layout.supports)
    packaged = app_layout.packaged
    level = const.SHOW_LEVEL
    runtime_spec = None
    if platform in {"win32", "darwin"}:
        runtime_spec = resolve_service_runtime(
            platform=platform,
            supports=supports,
            level=level,
            packaged=packaged,
        )

    if isinstance(command, DoctorCommand):
        doctor_report = diagnose(DoctorContext(
            platform=platform,
            entry_mode=app_layout.mode,
            entry_root=app_layout.root,
            home=mind_home(),
            config_path=mind_config_path(),
            mcp_config_path=mind_mcp_servers_path(),
            supports=app_layout.supports,
            packaged=packaged,
            runtime_spec=runtime_spec,
        ))
        frontend.application.emit(ApplicationView(
            type="json" if command.output_format == "json" else "doctor",
            renderable=(
                doctor_report.to_dict()
                if command.output_format == "json"
                else render_doctor_report(doctor_report)
            ),
        ))
        return doctor_report.exit_code

    if runtime_spec is None:
        raise MindError(
            f"{const.APP_DESC} is not supported on this platform: {platform}."
        )

    home = ensure_mind_home()

    src_opera_place = str(home)
    src_total_place = str(mind_reports_dir())

    ensure_mcp_servers_file()

    report = RunReport(src_total_place)
    power = os.cpu_count() or 1
    observe(
        "app.start",
        version=const.APP_VERSION,
        platform=platform,
        output_mode=output_mode,
        cpu_count=power,
        helix_requested=command_uses_helix(command),
        upgrade=isinstance(command, HelixUpgradeCommand),
    )

    try:
        pref = Preferences(str(mind_config_path()))

        # Notes: ========== 工具路径 ==========
        service_runtime_context = ServiceRuntimeContext(
            spec=runtime_spec,
            platform=platform,
            packaged=packaged,
            env_symbol=env_symbol,
            app_desc=const.APP_DESC
        )

        route_shell_tools(supports)
        clear_exec_env_cache()
        observe(
            "runtime.resolved",
            packaged=packaged,
            executable=runtime_spec.executable,
        )
    except BaseException as error:
        observe_exception("app.bootstrap.failed", error)
        report.close()
        raise

    # Notes: ========== 升级流程 ==========
    if isinstance(command, HelixUpgradeCommand):
        try:
            await ensure_service_runtime_asset(
                service_runtime_context,
                explicit_upgrade=True,
                anim_manager=entry_anim_manager,
                design=design,
            )
            observe("upgrade.complete")
            return 0
        except BaseException as error:
            observe_exception("upgrade.failed", error)
            raise
        finally:
            report.close()

    keywords = {
        "src_opera_place" : src_opera_place,
        "src_total_place" : src_total_place,
        "pref"            : pref,
        "anim_manager"    : entry_anim_manager,
        "animate"         : output_mode_uses_animation(output_mode),
        "frontend"        : frontend,
        "design"          : design,
        "report"          : report,
    }

    # remote = await global_config_task
    remote = {}

    try:
        server: ServerManage = ServerManage(
            runtime_spec.launch_command,
            env=process_env(),
        )
        mind = Mind(level, power, remote, **keywords)
    except BaseException as error:
        observe_exception("app.initialize.failed", error)
        report.close()
        raise

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
        observe("frontend.opened", output_mode=output_mode)

        if command_uses_helix(command) and output_mode != "tui":
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
            observe(
                "startup.ready",
                external_mcp=bool(
                    mind.external_mcp is not None
                    and mind.external_mcp.group is not None
                ),
                helix_linked=mind.is_service_mcp_linked(),
            )
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
            if command_uses_helix(command):
                start_helix = await confirm_tui_service_runtime_startup(mind)
                if not start_helix:
                    _emit_helix_skipped(mind)

            if start_helix:
                runtime.start_background_task(
                    _start_tui_service_runtime(mind),
                    name="mind tui service runtime startup",
                )

        await run_selected_mode(mind, command)

        completed = True
        observe("app.complete", exit_code=mind.exit_code)

        return mind.exit_code

    except asyncio.CancelledError:
        observe("app.interrupted", level="WARNING", output_mode=output_mode)
        raise
    except BaseException as error:
        observe_exception("app.failed", error, output_mode=output_mode)
        raise

    finally:
        await _finalize_mind(
            mind,
            output_mode=output_mode,
            completed=completed,
        )


if __name__ == '__main__':
    pass
