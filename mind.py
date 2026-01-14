#  __  __ _           _
# |  \/  (_)_ __   __| |
# | |\/| | | '_ \ / _` |
# | |  | | | | | | (_| |
# |_|  |_|_|_| |_|\__,_|
#

import os
import re
import sys
import json
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


class Mind(object):
    """Mind"""

    __remote: dict = {}

    def __init__(self, wires: list, level: str, power: int, remote: dict, *args, **kwargs):
        self.wires = wires  # 命令参数
        self.level = level  # 日志级别
        self.power = power  # 最大进程

        self.remote: dict = remote or {}  # workflow: 远程全局配置

        _, _ = args, kwargs

        self.task_event: asyncio.Event = asyncio.Event()
        self.url = "http://127.0.0.1:3333/mcp"

        self.sse: typing.Callable[
            [dict], str
        ] = lambda x: f"data: {json.dumps(x, ensure_ascii=False)}\n\n"

    @property
    def remote(self) -> dict:
        """Remote"""
        return self.__remote

    @remote.setter
    def remote(self, value: dict) -> None:
        """Remote"""
        self.__remote = value if isinstance(value, dict) else {}

    def signal_processor(self, *_, **__) -> None:
        """Signal Processor"""
        Design.console.print()
        Design.show_exit()
        logger.info(f"☎️ SYNC ▸ {const.APP_DESC} MCP neural core detaching.")
        self.task_event.set()
        sys.exit(130)

    async def exec_looper(self, plan: dict, steps: list, session: ClientSession) -> typing.AsyncGenerator[str, None]:
        """Exec Looper"""
        loop_count = plan.get("loop_count", 1)

        yield self.sse({"type": "exec", "tips": f"Loop Count -> {loop_count}"})

        for i in range(loop_count):
            for step in steps:
                action = step["action"]
                result = await session.call_tool(name := action["action"], action["args"])

                if result.isError:
                    yield self.sse({"type": "error", "tips": result.content[0].text}); return
                yield self.sse({"type": "exec", "tips": f"{name} -> {result.structuredContent}"})

        yield self.sse({"type": "exec", "tips": "done"})

    async def mind_trip(self, message: str, model: typing.Optional[str] = "llama-3.3-70b-versatile") -> None:
        """Mind Trip"""
        async with streamable_http_client(self.url) as (r, w, _):
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

                # workflow: ==== Request Streaming ====
                async for plan in request.stream_planner(payload):
                    if plan.get("type") == "error":
                        return logger.error(f"🔴 Error {plan.get('message')}")

                    if not (steps := plan.get("steps")):
                        continue

                    # workflow: ==== Exec Streaming ====
                    async for line in self.exec_looper(plan, steps, session):
                        try:
                            exec_event = json.loads(line[len("data:"):].strip())
                        except json.JSONDecodeError:
                            continue

                        if exec_event.get("type") == "error":
                            return logger.error(f"🔴 {exec_event.get('tips')}")
                        logger.info(f"🔶 {exec_event.get('tips')}")

    async def mind_loop(self) -> None:
        """Mind Loop"""

        def exchange_model() -> typing.Optional[str]:
            if (model_name := m.group(1).strip() if m.group(1) else None) and model_name in model_list:
                Design.console.print(f"[bold #5FFF87]🧬 Model switched to: {model}")
                return model_name

            for name in model_list:
                Design.console.print(f"[bold #5FFF87]  • {name}[/]")

            return Design.console.print(f"[bold #FF5F5F]\n🚫 Model invalid: /model {const.ERR}{model_name}")

        model_list = [
            "compound-beta",
            "compound-beta-mini",
            "gemma2-9b-it",
            "llama-3.1-8b-instant",
            "llama-3.3-70b-versatile",
            "meta-llama/llama-4-maverick-17b-128e-instruct",
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "meta-llama/llama-guard-4-12b",
            "moonshotai/kimi-k2-instruct",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "qwen/qwen3-32b"
        ]

        model = "llama-3.3-70b-versatile"

        quit_set: set[str] = {"/quit", "/q", "quit", "exit"}
        help_set: set[str] = {"/help", "/h"}

        doc = """\
        [bold]
        [bold #87FFAF]/help, /h[/]                 显示帮助
        [bold #FFD75F]/quit, /q, quit, exit[/]     退出
        [bold #5FD7FF]/again N <goal>[/]           将目标重复执行 N 次
        [bold #D7AFFF]/model <name>[/]             切换模型
        [/]"""

        re_model = re.compile(r"^\s*/model(?:\s+(.*))?\s*$", re.IGNORECASE)
        re_again = re.compile(r"^\s*/again\s+(\d+)\s+(.+?)\s*$", re.IGNORECASE)

        while not self.task_event.is_set():
            ask = f"\n[bold #D7FFAF]🤔 <{model}>[/]\n[bold #AFD7FF]ready 输入目标或 /help[/]"

            if (raw := Prompt.ask(ask, console=Design.console).strip()) in quit_set:
                break

            if raw in help_set:
                Design.console.print(doc); continue

            if m := re_model.match(raw):
                model = exchange_model() or model; continue

            try:
                message = f"{hit.group(2).strip()}，循环 {int(hit.group(1))} 次" if (
                    hit := re_again.match(raw)
                ) else raw; await self.mind_trip(message, model)

            except MindError as e: logger.warning(e)
            except Exception as e: logger.warning(e)


# """Main"""
async def main() -> None:
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

    async def privileged() -> typing.Any:
        if platform == "win32":
            pwsh = shutil.which("pwsh") or shutil.which("powershell")
            if not pwsh: return None
            cmd = [
                pwsh, "-Command", "Get-NetTCPConnection", "-LocalPort", "3333",
                "-ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
            ]
        else:
            cmd = ["lsof", "-ti", ":3333", "|", "xargs", "kill", "-9"]

        return await Terminal.cmd_line(cmd)

    # Notes: ========== Start from here ==========
    Design.startup_logo()

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

    if not os.path.exists(
        src_opera_place := os.path.join(initial_source, const.SRC_OPERA_PLACE).format()
    ):
        os.makedirs(src_opera_place, exist_ok=True)

    # 激活日志
    Active.active(level := "DEBUG" if cmd_lines.horizon else "INFO")

    # Notes: ========== 授权流程 ==========
    # todo

    # Notes: ========== 工具路径设置 ==========
    if platform == "win32":
        supports = os.path.join(turbo, "helix.dist").format()
        helix = os.path.join(supports, "helix.exe")
    elif platform == "darwin":
        supports = os.path.join(turbo, "helix.app").format()
        helix = os.path.join(supports, "Contents", "MacOS", "helix")
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

    # 清理端口
    await privileged()

    # 启动服务
    server: ServerManage = ServerManage()

    # ========== 本地调试 ==========
    root = Path(__file__).parent
    helix = str(root / "backend" / "helix.py")

    if helix.endswith("py"):
        await server.mcp_begin([sys.executable, helix, "--level", level])
    else:
        await server.mcp_begin([helix, "--level", level])

    positions = (
        cmd_lines.exec, cmd_lines.horizon
    )
    keywords = {}
    remote = {}

    mind = Mind(wires, level, power, remote, *positions, **keywords)

    signal.signal(signal.SIGINT, mind.signal_processor)

    try:
        if ex := cmd_lines.exec:
            await mind.mind_trip(ex)
        else:
            await mind.mind_loop()
    finally:
        await server.mcp_final()


# """Test"""
async def test() -> None:
    pass


if __name__ == '__main__':
    #  __  __ _           _
    # |  \/  (_)_ __   __| |
    # | |\/| | | '_ \ / _` |
    # | |  | | | | | | (_| |
    # |_|  |_|_|_| |_|\__,_|
    #

    # asyncio.run(test())

    try:
        main_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(main_loop)
        main_loop.run_until_complete(main())
    except MindError as _error:
        Design.Doc.err(_error)
        Design.show_fail()
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(Design.show_exit())
    except asyncio.CancelledError:
        sys.exit(Design.show_done())
    else:
        sys.exit(Design.show_done())
