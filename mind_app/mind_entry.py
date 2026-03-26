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
from mind_core import authorize
from mind_core.api import Api
from mind_core.design import Design
from mind_core.parser import Parser
from mind_core.preference import Preferences
from mind_nova import const

from .mind_core import Mind


async def main(entry_file: typing.Optional[str] = None) -> None:
    """应用入口：完成环境初始化、服务准备和命令分发。"""

    async def ensure_tool_permissions() -> None:
        """在 macOS 上为必要的辅助工具添加执行权限。"""
        if platform != "darwin":
            return None

        existing_tools = [t for t in [helix] if Path(t).exists()]

        tools_needing_auth = [
            t for t in existing_tools if not (Path(t).stat().st_mode & stat.S_IXUSR)
        ]

        if not tools_needing_auth:
            return None

        for t in tools_needing_auth:
            logger.debug(f"授权工具: {t}")

        for chmod_result in await asyncio.gather(
            *(Terminal.cmd_line(["chmod", "+x", t]) for t in tools_needing_auth),
            return_exceptions=True
        ):
            logger.debug(f"授权结果: {chmod_result}")

    await Design.particle_aggregate()

    parser = Parser()

    cli_args = parser.parse_cmd

    cli_wires = sys.argv[1:]

    platform = sys.platform.strip().lower()
    software = os.path.basename(os.path.abspath(sys.argv[0])).strip().lower()
    sys_symbol = os.sep
    env_symbol = os.path.pathsep

    if software == f"{const.APP_NAME}.exe":
        app_root = os.path.dirname(os.path.abspath(sys.argv[0]))
        workspace_root = os.path.dirname(app_root)
    elif software == f"{const.APP_NAME}":
        app_root = os.path.dirname(sys.executable)
        workspace_root = os.path.dirname(app_root)
    elif software == f"{const.APP_NAME}.py":
        source_file = entry_file or __file__
        app_root = os.path.dirname(os.path.abspath(source_file))
        workspace_root = app_root
    else:
        raise MindError(f"{const.APP_DESC} compatible with {const.APP_NAME} command")

    support_root = os.path.join(app_root, const.SCHEMATIC, const.SUPPORTS).format()

    if not os.path.exists(initial_source := os.path.join(workspace_root, const.STRUCTURE).format()):
        os.makedirs(initial_source, exist_ok=True)

    if not os.path.exists(src_opera_place := os.path.join(initial_source, const.SRC_OPERA_PLACE).format()):
        os.makedirs(src_opera_place, exist_ok=True)

    if not os.path.exists(src_total_place := os.path.join(initial_source, const.SRC_TOTAL_PLACE).format()):
        os.makedirs(src_total_place, exist_ok=True)

    Active.active(level := "DEBUG" if cli_args.reflection else "INFO")

    pref_file = os.path.join(initial_source, const.SRC_OPERA_PLACE, const.PREF)
    pref = Preferences(pref_file)

    if platform == "win32":
        supports = os.path.join(support_root, "windows").format()
        helix = os.path.join(supports, "helix.dist", "helix.exe")
    elif platform == "darwin":
        supports = os.path.join(support_root, "macos").format()
        helix = os.path.join(supports, "helix.app", "Contents", "MacOS", "helix")
    else:
        raise MindError(f"{const.APP_DESC} is not supported on this platform: {platform}.")

    if cli_args.upgrade:
        upgrader = Upgrade()
        return await upgrader.upgrade_app(supports)

    tool_paths = [helix]
    for tool_path in tool_paths:
        os.environ["PATH"] = os.path.dirname(tool_path) + env_symbol + os.environ.get("PATH", "")

    for tool_path in tool_paths:
        if not shutil.which((tool_name := os.path.basename(tool_path))):
            raise MindError(f"{const.APP_DESC} missing files {tool_name}")

    await ensure_tool_permissions()

    launch_cmd = [helix, "--level", level]

    if cli_args.pref:
        server: ServerManage = ServerManage(launch_cmd)
        await server.ensure_running()
        await server.close()
        return await FileAssist.open_url(f"{const.BASE_URL}/pref")

    lic_file = Path(src_opera_place) / const.LIC_FILE

    if apply_code := cli_args.apply:
        return await authorize.receive_license(apply_code, lic_file)

    await authorize.verify_license(lic_file)

    global_config_task = asyncio.create_task(Api.remote_config())

    logger.debug(f"{'=' * 15} 系统调试 {'=' * 15}")
    logger.debug(f"操作系统: {platform}")
    logger.debug(f"核心数量: {(power := os.cpu_count())}")
    logger.debug(f"应用名称: {software}")
    logger.debug(f"系统路径: {sys_symbol}")
    logger.debug(f"环境变量: {env_symbol}")
    logger.debug(f"日志等级: {level}")
    logger.debug(f"工具目录: {support_root}")
    logger.debug(f"{'=' * 15} 系统调试 {'=' * 15}\n")

    logger.debug(f"{'=' * 15} 环境变量 {'=' * 15}")
    for env_path in os.environ["PATH"].split(env_symbol):
        logger.debug(f"PATH: {env_path}")
    logger.debug(f"{'=' * 15} 环境变量 {'=' * 15}\n")

    logger.debug(f"{'=' * 15} 工具路径 {'=' * 15}")
    for tool_path in tool_paths:
        logger.debug(f"工具: {tool_path}")
    logger.debug(f"{'=' * 15} 工具路径 {'=' * 15}\n")

    server: ServerManage = ServerManage(launch_cmd)
    await server.ensure_running()
    await server.close()
    await pref.load_pref()

    Design.Doc.log(f"[bold #0EA5E9]🌐 Link: {const.BASE_URL}[/]\n")

    positions = (
        cli_args.chat,
        cli_args.fast,
        cli_args.plan,
        cli_args.gravity,
        cli_args.reflection,
        cli_args.code
    )
    init_kwargs = {
        "src_opera_place": src_opera_place,
        "src_total_place": src_total_place,
        "pref": pref
    }
    remote = await global_config_task

    mind = Mind(cli_wires, level, power, remote, *positions, **init_kwargs)

    signal.signal(signal.SIGINT, mind.signal_processor)

    if chat := cli_args.chat:
        await mind.calling(message=chat, func=mind.mind_chat, mode="chat")
    elif fast := cli_args.fast:
        await mind.calling(message=fast, func=mind.mind_fast, mode="fast")
    elif plan := cli_args.plan:
        await mind.calling(message=plan, func=mind.mind_plan, mode="plan")
    elif code := cli_args.code:
        if cli_args.chat is not None:
            runner = mind.stream_looper
            mode: typing.Literal["chat"] = "chat"
        elif cli_args.fast is not None:
            runner = mind.stream_looper
            mode: typing.Literal["fast"] = "fast"
        else:
            runner = mind.static_looper
            mode: typing.Literal["plan"] = "plan"

        await mind.mind_pack(code, mode, runner)
    else:
        await mind.mind_loop()


async def test() -> None:
    """测试入口占位：当前未定义独立测试流程。"""
    pass


if __name__ == '__main__':
    pass
