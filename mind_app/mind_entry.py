# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import sys
import stat
import shutil
import signal
import typing
import asyncio
from pathlib import Path
from loguru import logger
from engine.manage import ServerManage
from engine.tinker import (
    MindError, Active, FileAssist
)
from engine.terminal import Terminal
from engine.upgrade import Upgrade
# from mind_core import authorize
# from mind_core.api import Api
from mind_core.design import Design
from mind_core.design.upload import UploadProgressLiveReporter
from mind_core.parser import Parser
from mind_core.preference import Preferences
from mind_nova import const
from mind_nova.modes import RunMode
from .mcp import mcp_servers_path
from .mind_core import Mind


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
        Design.console.print(reporter.render_failure(message=str(error), event=reporter.last_event))
        raise
    finally:
        await mind.await_cleanup(mind.stop_anim())

    mind.attach.clear_pending_attachments()

    if reporter.last_event is not None:
        Design.console.print(reporter.render_summary(reporter.last_event))

    return uploaded


async def main(entry_file: typing.Optional[str] = None) -> int:
    """Main"""
    async def authorized() -> None:
        if platform != "darwin":
            return None

        tools_set = [kit for kit in [helix] if Path(kit).exists()]

        ensure = [
            kit for kit in tools_set if not (Path(kit).stat().st_mode & stat.S_IXUSR)
        ]

        if not ensure:
            return None

        for auth in ensure:
            logger.debug(f"Authorizing: {auth}")

        for resp in await asyncio.gather(
            *(Terminal.cmd_line(["chmod", "+x", kit]) for kit in ensure), return_exceptions=True
        ):
            logger.debug(f"Authorize: {resp}")

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
    turbo = os.path.join(mind_work, const.SCHEMATIC, const.SUPPORTS).format()

    if not os.path.exists(
        initial_source := os.path.join(mind_feasible, const.STRUCTURE).format()
    ):
        os.makedirs(initial_source, exist_ok=True)

    if not os.path.exists(
        src_opera_place := os.path.join(initial_source, const.SRC_OPERA_PLACE).format()
    ):
        os.makedirs(src_opera_place, exist_ok=True)

    if not os.path.exists(
        src_total_place := os.path.join(initial_source, const.SRC_TOTAL_PLACE).format()
    ):
        os.makedirs(src_total_place, exist_ok=True)

    mcp_file = mcp_servers_path(src_opera_place)
    if not mcp_file.exists():
        mcp_file.write_text('{\n  "mcpServers": {}\n}\n', encoding="utf-8")

    # Notes: ========== 激活日志 ==========
    Active.active(level := "DEBUG" if cmd_lines.reflection else "INFO")

    pref_file = os.path.join(initial_source, const.SRC_OPERA_PLACE, const.PREF)
    pref = Preferences(pref_file)

    # Notes: ========== 工具路径 ==========
    if platform == "win32":
        supports = os.path.join(turbo, "windows").format()
        helix = os.path.join(supports, "helix.dist", "helix.exe")
    elif platform == "darwin":
        supports = os.path.join(turbo, "macos").format()
        helix = os.path.join(supports, "helix.app", "Contents", "MacOS", "helix")
    else:
        raise MindError(f"{const.APP_DESC} is not supported on this platform: {platform}.")

    # Notes: ========== 升级流程 ==========
    if cmd_lines.upgrade:
        up: Upgrade = Upgrade()
        await up.upgrade_app(supports)
        return 0

    for tls in (tools := [helix]):
        os.environ["PATH"] = os.path.dirname(tls) + env_symbol + os.environ.get("PATH", "")

    # Notes: ========== 检查工具 ==========
    if not software.endswith(".py"):
        for tls in tools:
            if not shutil.which((tls_name := os.path.basename(tls))):
                raise MindError(f"{const.APP_DESC} missing files {tls_name}")

    # Notes: ========== 三方应用 ==========
    await authorized()

    # Notes: ========== 启动命令 ==========
    if not software.endswith(".py"):
        launch_cmd = [helix, "--level", level]
    else:
        launch_cmd = [sys.executable, "-m", "backend.helix", "--level", level]

    # if cmd_lines.pref:
    if cmd_lines.hello:
        server: ServerManage = ServerManage(launch_cmd)
        await server.ensure_running()
        await server.close()
        # return await FileAssist.open_url(f"{const.BASE_URL}/pref")
        await FileAssist.open_url(const.BASE_URL)
        return 0

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
    for tls in tools:
        logger.debug(f"TLS: {tls}")
    logger.debug(f"{'=' * 15} 工具路径 {'=' * 15}\n")

    server: ServerManage = ServerManage(launch_cmd)
    await server.ensure_running()
    await pref.load_pref()

    # Design.Doc.log(f"[bold #0EA5E9]🌐 {const.BASE_URL}[/]\n")

    positions = (
        cmd_lines.chat, cmd_lines.fast, cmd_lines.plan, cmd_lines.xtra,
        cmd_lines.gravity, cmd_lines.reflection, cmd_lines.code
    )
    keywords = {
        "src_opera_place" : src_opera_place,
        "src_total_place" : src_total_place,
        "pref"            : pref
    }

    # remote = await global_config_task
    remote = {}

    mind = Mind(wires, level, power, remote, *positions, **keywords)
    mind.bind_runtime(asyncio.get_running_loop(), asyncio.current_task())
    mind.bind_server_manager(server)
    mind.start_keepalive_supervisor()
    await mind.start_external_mcp_runtime()

    signal.signal(signal.SIGINT, mind.signal_processor)

    try:
        cli_attachments = await resolve_cli_attachments(mind, cmd_lines)

        if cmd_lines.agent:
            await mind.agent_loop()
        elif chat := cmd_lines.chat:
            await mind.calling(message=chat, mode="chat", attachments=cli_attachments)
        elif fast := cmd_lines.fast:
            await mind.calling(message=fast, mode="fast", attachments=cli_attachments)
        elif plan := cmd_lines.plan:
            await mind.calling(message=plan, mode="plan")
        elif xtra := cmd_lines.xtra:
            await mind.calling(message=xtra, mode="xtra", attachments=cli_attachments)
        elif code := cmd_lines.code:
            mode = resolve_code_mode(cmd_lines)
            await mind.mind_pack(code, mode)

        else:
            await mind.mind_loop()
    finally:
        await mind.close_runtime_resources()

    return mind.exit_code


if __name__ == '__main__':
    pass
