#  __  __ _           _
# |  \/  (_)_ __   __| |
# | |\/| | | '_ \ / _` |
# | |  | | | | | | (_| |
# |_|  |_|_|_| |_|\__,_|
#

import os
import re
import sys
import stat
import shutil
import signal
import typing
import asyncio
from pathlib import Path
from loguru import logger
from rich.prompt import Prompt
from mcp import (
    ClientSession, ListToolsResult
)
from mcp.client.streamable_http import streamable_http_client
from mindcore.design import Design
from engine.manage import (
    ServerManage, DeviceManage
)
from engine.tinker import (
    MindError, Active
)
from engine.terminal import Terminal
from mindcore.parser import Parser
from utils import (
    const, request
)


def signal_processor(*_, **__) -> None:
    Design.console.print()
    logger.info(f"📞 Received signal {signal.SIGINT} ...")
    sys.exit(0)


async def mind_trip(message: str, model: str = "llama-3.1-8b-instant") -> None:

    async def exec_looper() -> None:
        for i in range(plan.get("loop_count", 1)):
            for step in steps:
                action = step["action"]
                result = await session.call_tool(action["action"], action["args"])

                if result.isError: return logger.error(f"{result.structuredContent}")
                else: logger.info(f"{result.structuredContent}")

    # device_list = await DeviceManage().refresh()
    # for device in device_list: logger.debug(device)

    async with streamable_http_client("http://127.0.0.1:3333/mcp") as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()

            list_tools: ListToolsResult = await session.list_tools()

            openai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description" : tool.description,
                        "parameters"  : tool.inputSchema
                    }
                }
                for tool in list_tools.tools
            ]

            for tool in openai_tools: logger.debug(f"⚙️ Tool {tool['function']['name']}")

            payload = {"model": model, "message": message, "tools": openai_tools}

            async for plan in request.stream_planner(payload):
                if not (steps := plan.get("steps")):
                    continue
                await exec_looper()


async def mind_loop() -> None:
    doc = """\

    /help              显示帮助
    /quit              退出
    /repeat N <goal>   将目标重复执行 N 次
    /tools             查看可用工具（如果你有输出方法）
    """

    ask = "[bold #AFD7FF]\n🤔 输入目标或 /help[/]"

    while (raw := Prompt.ask(ask, console=Design.console)) not in {"/quit", "quit", "exit"}:
        if raw.strip() in {"/help", "help"}:
            Design.console.print(doc); continue

        try:
            if m := re.match(r"^/repeat\s+(\d+)\s+(.+)$", raw.strip()):
                await mind_trip(f"{m.group(2)}，循环 {int(m.group(1))} 次"); continue
            await mind_trip(raw)

        except MindError as e: logger.warning(e)
        except Exception as e: logger.error(e)


async def main() -> None:

    async def authorized() -> None:
        """
        检查目录下的所有文件是否具备执行权限，如果文件没有执行权限，则自动添加 +x 权限。
        """
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
    Design.startup_logo()

    # 解析命令行参数
    parser = Parser()
    cmd_lines = parser.parse_cmd

    # 如果没有提供命令行参数，则显示帮助文档，并退出程序
    # if len(system_parameter_list := sys.argv) == 1:
    #     return parser.parse_engine.print_help()

    # 获取命令行参数（去掉第一个参数，即脚本名称）
    # wires = system_parameter_list[1:]

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
        mind_work = os.path.dirname(os.path.abspath(__file__))
        mind_feasible = mind_work
    else:
        raise MindError(f"{const.APP_DESC} compatible with {const.APP_NAME} command")

    # Notes: ========== 路径初始化 ==========
    turbo = os.path.join(mind_work, const.SCHEMATIC, const.SUPPORTS).format()

    if not os.path.exists(
        initial_source := os.path.join(mind_feasible, const.STRUCTURE).format()
    ):
        os.makedirs(initial_source, exist_ok=True)

    Active.active(level := "DEBUG" if cmd_lines.horizon else "INFO")

    # Notes: ========== 工具路径设置 ==========
    if platform == "win32":
        supports = os.path.join(turbo, "helix.dist").format()
        helix = "helix.exe"
        helix = os.path.join(supports, helix)
    elif platform == "darwin":
        supports = os.path.join(turbo, "helix.app").format()
        helix = "helix"
        helix = os.path.join(supports, "Contents", "MacOS", helix)
    else:
        raise MindError(f"{const.APP_DESC} is not supported on this platform: {platform}.")

    for tls in (tools := [helix]):
        os.environ["PATH"] = os.path.dirname(tls) + env_symbol + os.environ.get("PATH", "")

    # 三方应用以及文件授权
    await authorized()

    # 检查每个工具是否存在，如果缺失则显示错误信息并退出程序
    # for tls in tools:
    #     if not shutil.which((tls_name := os.path.basename(tls))):
    #         raise MindError(f"{const.APP_DESC} missing files {tls_name}")

    # Notes: ========== 配置与启动 ==========

    # 远程全局配置
    # todo
    # 启动仪式
    # todo

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

    root = Path(__file__).parent
    helix = str(Path(root, "backend", "helix.py"))

    server = ServerManage()

    if helix.endswith("py"):
        await server.mcp_begin([sys.executable, str(helix)])
    else:
        await server.mcp_begin([str(helix)])

    signal.signal(signal.SIGINT, signal_processor)

    try:
        if ex := cmd_lines.exec: await mind_trip(ex)
        else: await mind_loop()

    except MindError as e: logger.warning(e)
    except Exception as e: logger.error(e)

    finally: await server.mcp_final()


if __name__ == '__main__':
    asyncio.run(main())
