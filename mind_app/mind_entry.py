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
from engine.tinker import (
    MindError, Active
)
# from mind_core import authorize
# from mind_core.api import Api
from mind_core.design import Design
from mind_core.design.upload import UploadProgressLiveReporter
from mind_core.parser import Parser
from mind_core.preference import Preferences
from mind_core.service_config import ServiceConfig
from mind_nova import const
from mind_nova.services import service_endpoints
from mind_nova.modes import RunMode
from .mind_core import Mind
from .modes.support.repl_prompt import fetch_runtime_workspace_root
from .runtime.environment.exec_env import clear_exec_env_cache
from .runtime.environment.shell_tools import route_shell_tools
from .runtime.mcp.service_runtime import (
    authorize_runtime_files,
    ensure_runtime_asset,
    prepend_runtime_paths,
    resolve_service_runtime,
    start_service_runtime,
    verify_runtime_paths
)
from .paths import (
    ensure_mcp_servers_file,
    ensure_mind_home,
    mind_config_path,
    mind_reports_dir,
    process_env
)


def resolve_code_mode(cmd_lines: typing.Any) -> RunMode:
    if cmd_lines.chat is not None:
        return "chat"
    if cmd_lines.fast is not None:
        return "fast"
    if cmd_lines.plan is not None:
        return "plan"
    if cmd_lines.xtra is not None:
        return "xtra"

    raise MindError("--code requires --chat, --fast, --plan, or --xtra")


async def resolve_cli_attachments(
    mind: Mind,
    cmd_lines: typing.Any
) -> typing.Optional[list[dict[str, typing.Any]]]:
    raw_attachments = cmd_lines.attach or []
    if not raw_attachments:
        return None

    if cmd_lines.code:
        raise MindError("--attach is not supported together with --code yet")

    if cmd_lines.plan is not None:
        raise MindError("--attach is not supported with --plan")

    if cmd_lines.chat is None and cmd_lines.fast is None and cmd_lines.xtra is None:
        raise MindError("--attach requires --chat, --fast, or --xtra")

    for raw_path in raw_attachments:
        mind.attach.add_pending_attachments(raw_path)

    pending  = mind.attach.pending_attachments_snapshot()
    reporter = UploadProgressLiveReporter(Design.console)

    upload_state: dict[str, typing.Any] = {
        "event"       : None,
        "item_total"  : len(pending),
        "total_bytes" : sum(int(item.get("size") or 0) for item in pending)
    }

    async def capture_progress(event: dict[str, typing.Any]) -> None:
        reporter.last_event = dict(event)
        upload_state["event"] = dict(event)

    try:
        await mind.start_upload_anim(lambda: dict(upload_state))
        uploaded = await mind.attach.upload_pending_attachments(progress_callback=capture_progress)
    except MindError as error:
        failure_reason = str(getattr(error, "display_reason", "") or error)
        Design.console.print(reporter.render_failure(message=failure_reason, event=reporter.last_event))
        raise

    finally:
        await mind.await_cleanup(mind.stop_anim())

    mind.attach.clear_pending_attachments()

    if reporter.last_event is not None:
        Design.console.print(reporter.render_summary(reporter.last_event))

    return uploaded


async def run_selected_mode(
    mind: Mind,
    cmd_lines: typing.Any,
    cli_attachments: typing.Optional[list[dict[str, typing.Any]]]
) -> None:
    """按命令行参数分派到单次调用、批处理、订阅或交互模式。"""
    access_mode = cmd_lines.access

    if cmd_lines.agent:
        await mind.agent_loop()
    elif chat := cmd_lines.chat:
        await mind.calling(message=chat, mode="chat", attachments=cli_attachments, access_mode=access_mode)
    elif fast := cmd_lines.fast:
        await mind.calling(message=fast, mode="fast", attachments=cli_attachments, access_mode=access_mode)
    elif plan := cmd_lines.plan:
        await mind.calling(message=plan, mode="plan", access_mode=access_mode)
    elif xtra := cmd_lines.xtra:
        await mind.calling(message=xtra, mode="xtra", attachments=cli_attachments, access_mode=access_mode)
    elif code := cmd_lines.code:
        mode = resolve_code_mode(cmd_lines)
        await mind.mind_pack(code, mode, access_mode=access_mode)
    else:
        await mind.mind_loop()


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


async def _run_main(
    entry_file: typing.Optional[str],
    entry_anim_manager: AsyncAnimManager,
    handler: SignalHandler | None = None
) -> int:
    """执行入口主流程。"""
    # Notes: ========== Start from here ==========
    # await Design.particle_aggregate()
    Design.show_intro()

    # 解析命令行参数
    parser = Parser()
    cmd_lines = parser.parse_cmd

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
    Active.active(level := "DEBUG" if cmd_lines.reflection else "INFO")

    pref = Preferences(str(mind_config_path()))

    # Notes: ========== 工具路径 ==========
    if platform == "win32":
        supports = os.path.join(turbo, "windows").format()
    elif platform == "darwin":
        supports = os.path.join(turbo, "macos").format()
    else:
        raise MindError(f"{const.APP_DESC} is not supported on this platform: {platform}.")

    runtime_spec = resolve_service_runtime(
        platform=platform,
        supports=supports,
        level=level,
        packaged=not software.endswith(".py")
    )

    route_shell_tools(supports)
    clear_exec_env_cache()

    # Notes: ========== 升级流程 ==========
    if cmd_lines.upgrade:
        await ensure_runtime_asset(
            runtime_spec,
            software=software,
            explicit_upgrade=True,
            anim_manager=entry_anim_manager
        )
        return 0

    if cmd_lines.helix:
        await ensure_runtime_asset(
            runtime_spec,
            software=software,
            explicit_upgrade=False,
            anim_manager=entry_anim_manager
        )

        prepend_runtime_paths(runtime_spec, env_symbol=env_symbol)

        # Notes: ========== 检查工具 ==========
        verify_runtime_paths(
            runtime_spec,
            packaged=not software.endswith(".py"),
            app_desc=const.APP_DESC
        )

        # Notes: ========== 三方应用 ==========
        await authorize_runtime_files(runtime_spec, platform=platform)

    # Notes: ========== 授权流程 ==========
    # lic_file = Path(src_opera_place) / const.LIC_FILE
    # if apply_code := cmd_lines.apply:
    #     return await authorize.receive_license(apply_code, lic_file)
    # await authorize.verify_license(lic_file)

    # Notes: ========== 远程配置 ==========
    # global_config_task = asyncio.create_task(Api.remote_config())

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

    positions = (
        cmd_lines.chat, cmd_lines.fast, cmd_lines.plan, cmd_lines.xtra,
        cmd_lines.gravity, cmd_lines.reflection, cmd_lines.code
    )
    keywords = {
        "src_opera_place" : src_opera_place,
        "src_total_place" : src_total_place,
        "pref"            : pref,
        "anim_manager"    : entry_anim_manager
    }

    # remote = await global_config_task
    remote = {}

    server: ServerManage = ServerManage(runtime_spec.launch_command, env=process_env())
    mind = Mind(wires, level, power, remote, *positions, **keywords)
    mind.bind_runtime(asyncio.get_running_loop(), asyncio.current_task())
    mind.bind_server_manager(server)

    if handler is not None:
        handler.bind_delegate(mind.signal_processor)

    try:
        if cmd_lines.helix:
            await start_service_runtime(mind)

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
            await mind.start_external_mcp_runtime()
            await pref_task
            service_endpoints.configure(await domain_task)
        finally:
            for task in startup_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*startup_tasks, return_exceptions=True)

        # Design.Doc.log(f"[bold #0EA5E9]🌐 {const.BASE_URL}[/]\n")

        cli_attachments = await resolve_cli_attachments(mind, cmd_lines)

        await run_selected_mode(mind, cmd_lines, cli_attachments)
        return mind.exit_code
    finally:
        await mind.close_runtime_resources()


if __name__ == '__main__':
    pass
