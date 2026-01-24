#  __  __ _           _
# |  \/  (_)_ __   __| |
# | |\/| | | '_ \ / _` |
# | |  | | | | | | (_| |
# |_|  |_|_|_| |_|\__,_|
#

# ====[ 内置模块 ]====
import os
import re
import sys
import json
import stat
import time
import httpx
import random
import shutil
import signal
import typing
import asyncio

# ====[ from: 内置模块 ]====
from pathlib import Path

# ====[ from: 第三方库 ]====
from loguru import logger
from rich.live import Live
from rich.text import Text
from rich.prompt import Prompt
from mcp import (
    ClientSession, ListToolsResult
)
from mcp.client.streamable_http import streamable_http_client

# ====[ from: 本地模块 ]====
from mindcore.api import Api
from mindcore.design import Design
from engine.manage import ServerManage
from engine.tinker import (
    MindError, Active
)
from engine.terminal import Terminal
from mindcore.parser import Parser
from mindcore.profile import Preferences
from mindnova import (
    authentic, const, request
)


class Mind(object):
    """Mind class."""

    __remote: dict = {}

    def __init__(self, wires: list, level: str, power: int, remote: dict, *args, **kwargs):
        self.wires = wires  # 命令参数
        self.level = level  # 日志级别
        self.power = power  # 最大进程

        self.remote: dict = remote or {}  # workflow: 远程全局配置

        _ = args
        self.pref: Preferences = kwargs["pref"]

        self.task_event: asyncio.Event = asyncio.Event()
        self.task_info: list = []

        self.animation_event: typing.Optional[asyncio.Event] = None
        self.animation_task: typing.Optional[asyncio.Task] = None

        self.last_refresh_ts = 0.0
        self.ttl_sec         = 1.0

        self.sse: typing.Callable[
            [dict], str
        ] = lambda x: f"data: {json.dumps(x, ensure_ascii=False)}\n\n"

        self.stream_out     = ""
        self.stream_delay   = 0.010
        self.stream_cursors = ["█", "▉", "▋"]

        self.design: Design = Design(self.level)

    @property
    def remote(self) -> dict:
        return self.__remote

    @remote.setter
    def remote(self, value: dict) -> None:
        self.__remote = value if isinstance(value, dict) else {}

    def signal_processor(self, *_, **__) -> None:
        """Signal Processor"""
        self.task_event.set()
        Design.console.print()
        Design.show_exit()
        logger.debug(f"SYNC ▸ {const.APP_DESC} MCP neural core detaching.")
        sys.exit(130)

    async def off_live_state(self) -> None:
        if self.animation_event: self.animation_event.set()
        if self.animation_task: await self.animation_task
        return self.task_info.clear()

    async def exec_status(self, session: ClientSession) -> typing.Optional[str]:
        """Exec Status"""
        if ((now := time.time()) - self.last_refresh_ts) < self.ttl_sec:
            return logger.debug(f"ttl-hit: skip refresh ttl={self.ttl_sec:.3f}s")

        tools = {
            "name": "refresh", "arguments": {"ttl_sec": self.ttl_sec}
        }

        if (result := await session.call_tool(**tools)).isError:
            return result.content[0].text

        self.last_refresh_ts = now
        return logger.debug(result.structuredContent)

    async def exec_looper(self, model, apikey, steps: list, loop_count: int, session: ClientSession) -> None:
        """Exec Looper"""
        for index, _ in enumerate(range(loop_count), start=1):
            for step in steps:
                action = step["action"]

                # ✅ 每次执行工具前，先确保 server 侧设备缓存是新的（TTL 控频）
                if error := await self.exec_status(session):
                    await self.off_live_state()
                    return logger.error(error)

                name, argument = action["action"], action["args"]

                logger.debug(tips := f"{name} -> args={argument}")
                self.task_info.append(tips)

                result = await session.call_tool(name, argument)
                fields: typing.Union[
                    dict[str, typing.Any], str
                ] = sc if (sc := result.structuredContent) else result.content[0].text

                if result.isError:
                    await self.off_live_state()
                    return logger.error(fields)

                if name in {"find_element"}:
                    locator_list = await self.mind_heal(model, apikey, fields["results"])
                    if locator_list and argument.get("should_click", False):
                        await asyncio.gather(
                            *(session.call_tool("click", s) for s in locator_list)
                        )

                logger.debug(tips := f"{name} -> resp={fields}")
                self.task_info.append(tips)

            if index != loop_count: self.task_info.clear()

    async def mind_heal(self, model: str, apikey: str, elements: list[dict]) -> typing.Optional[list[dict]]:
        """Mind Heal"""
        locator_list: list[dict[str, str]] = []

        for element in elements:
            async for heal in request.stream_heal(model, apikey, **element["data"]):
                if heal.get("type") == "error":
                    await self.off_live_state()
                    return logger.error(heal["content"])

                if smart := heal.get("smart"):
                    logger.debug(smart)
                    locator_list.append({
                        "by": smart["new_selector"]["primary"]["by"],
                        "value": smart["new_selector"]["primary"]["value"]
                    })

                self.task_info.append(heal["content"])

        return locator_list

    async def mind_plan(self, model: str, apikey: str, message: str) -> None:
        """Mind Plan"""
        if not model or not apikey:
            missing = ", ".join(
                x for x, ok in [("model", bool(model)), ("api_key", bool(apikey))] if not ok
            )
            raise MindError(f"Missing required field(s): {missing}")

        url = "http://127.0.0.1:3333/mcp"
        headers = {
            "Authorization": f"Bearer {authentic.manufacture_token()}"
        }
        timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
        event_hooks = {"response": [request.capture]}

        async with httpx.AsyncClient(headers=headers, timeout=timeout, event_hooks=event_hooks) as client:

            # workflow: ==== Tool Streaming ====
            async with streamable_http_client(url, http_client=client) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()

                    list_tools: ListToolsResult = await session.list_tools()

                    openai_tools: list[dict[str, typing.Any]] = [
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

                    for tool in openai_tools:
                        logger.debug(f"Tool {tool['function']['name']}")

                    # workflow: ==== Plan Streaming ====
                    async for plan in request.stream_plan(model, apikey, message, openai_tools):
                        if plan.get("type") == "error":
                            return logger.error(plan)

                        steps, loop_count = plan["steps"], plan["loop_count"]

                        # workflow: ==== Exec ====
                        self.animation_event = asyncio.Event()
                        self.animation_task = asyncio.create_task(
                            self.design.deep_thinking(self.task_info, self.animation_event)
                        )
                        await self.exec_looper(model, apikey, steps, loop_count, session)
                        await self.off_live_state()

    async def mind_chat(self, model: str, apikey: str, message: str) -> None:
        """Mind Chat"""
        if not model or not apikey:
            missing = ", ".join(
                x for x, ok in [("model", bool(model)), ("api_key", bool(apikey))] if not ok
            )
            raise MindError(f"Missing required field(s): {missing}")

        out    = self.stream_out
        delay  = self.stream_delay
        cursor = random.choice(self.stream_cursors)

        with Live(Text(), console=Design.console, refresh_per_second=60) as live:

            # workflow: ==== Chat Streaming ====
            async for chat in request.stream_chat(model, apikey, message):
                if chat.get("type") == "error":
                    return logger.error(chat.get("content"))

                out, delay = await Design.typewriter(
                    live, chat.get("content", ""), out, delay, max(0.0015, delay * 0.65), cursor=cursor
                )

            await Design.cursor_blink(live, out, cursor=cursor)

        Design.console.print()

    async def mind_loop(self) -> None:
        """Mind Loop"""

        async def exchange(types: typing.Literal["model", "apikey"]) -> typing.Optional[str]:
            if pref_name := m.group(1).strip() if m.group(1) else None:
                return pref_name

            styles: list[str] = []

            match types:
                case "model":
                    styles = [
                        "llama-3.3-70b-versatile", "openai/gpt-oss-120b", "gpt-4o-mini", "deepseek-chat"
                    ]
                case "apikey":
                    styles = [
                        "sk-...   (API Key)", "gsk_...  (API Key)", "ds-...   (API Key)", "<token>  (Pure token)"
                    ]

            for s in styles: Design.console.print(f"[bold {rc}]  • {s}[/]")
            return Design.console.print(f"[bold #FF5F5F]\n {types} invalid: /{types} {const.ERR}{pref_name}")

        cp = [
            "#5FFF87", "#87FFAF", "#5FD7FF", "#D7AFFF", "#FFD75F",
            "#FF5F5F", "#FF87D7", "#AF87FF", "#00D7AF", "#00AFFF",
            "#FFAF00", "#AFD7FF",
        ]
        rc = random.choice(cp)

        model  = self.pref.model
        apikey = self.pref.apikey

        quit_set: set[str] = {"/quit", "/q", "quit", "exit"}
        help_set: set[str] = {"/help", "/h"}

        doc = """\
        [bold]
        [bold #AFD7FF]/help, /h[/]                 指令索引（用法/示例/约定）
        [bold #FF5F5F]/quit, /q, quit, exit[/]     断开会话（安全退出）
        [bold #AFD7FF]/model <name>[/]             引擎切换（选择推理内核）
        [bold #AFD7FF]/apikey <key>[/]             凭证更新（替换访问密钥）
        [bold #AFD7FF]/again N <goal>[/]           复现回放（目标 × N 次）
        [bold #FFD75F]/chat[/]                     问答模式（自由对话）
        [bold #FFD75F]/plan[/]                     编排模式（工具执行）
        [bold #FFD75F]/fast[/]                     性能模式（压测采集）
        [/]"""

        re_again  = re.compile(r"^\s*/again\s+(\d+)\s+(.+?)\s*$", re.IGNORECASE)
        re_model  = re.compile(r"^\s*/model(?:\s+(.*))?\s*$", re.IGNORECASE)
        re_apikey = re.compile(r"^\s*/apikey(?:\s+(.*))?\s*$", re.IGNORECASE)

        # 主题
        tag: typing.Literal["CHAT", "PLAN", "FAST"] = "CHAT"

        theme = {
            "CHAT": {
                "banner": "╔═⟦ 𝕮𝖍𝖆𝖙 ⟧═╗",
                "prompt": "│ 〉Chat",
                "tag": "#FF87D7",
                "prompt_c": "#FFD75F",
                "model": "#FFAF5F",
                "ready": "#D7AFFF",
                "hint": "#FF87D7",
            },
            "PLAN": {
                "banner"   : "╔═⟦ 𝔓𝔩𝔞𝔫 ⟧═╗",
                "prompt"   : "│ 〉Plan",
                "tag"      : "#5FD7FF",
                "prompt_c" : "#87FFAF",
                "model"    : "#5FFF87",
                "ready"    : "#AFD7FF",
                "hint"     : "#5FD7FF",
            },
            "FAST": {
                "banner"   : "╔═⟦ 𝓕𝓪𝓼𝓽 ⟧═╗",
                "prompt"   : "│ 〉Fast",
                "tag"      : "#FFAF00",
                "prompt_c" : "#FFD75F",
                "model"    : "#FFAF00",
                "ready"    : "#AFD7FF",
                "hint"     : "#FFAF00",
            }
        }

        while not self.task_event.is_set():
            th = theme[tag]
            ask = (
                f"\n[bold {th['tag']}]{th['banner']}[/]"
                f"\n[bold {th['prompt_c']}]{th['prompt']}[/] [bold {th['model']}]<{model}>[/]"
                f"\n[bold {th['ready']}]ready 输入目标或 /help[/]"
            )

            try:
                if (raw := Prompt.ask(ask, console=Design.console).strip()) in quit_set:
                    break
            except (EOFError, UnicodeDecodeError):
                continue

            if raw in help_set:
                Design.console.print(doc)
                continue

            if raw.lower() == "/chat":
                tag = "CHAT"
                Design.console.print(f"[bold {theme['CHAT']['hint']}]Exchange → Chat[/]")
                continue

            if raw.lower() == "/plan":
                tag = "PLAN"
                Design.console.print(f"[bold {theme['PLAN']['hint']}]Exchange → Plan[/]")
                continue

            if raw.lower() == "/fast":
                tag = "FAST"
                Design.console.print(f"[bold {theme['FAST']['hint']}]Exchange → Fast[/]")
                continue

            if m := re_model.match(raw):
                model = await exchange("model") or model
                continue

            if m := re_apikey.match(raw):
                apikey = await exchange("apikey") or apikey
                continue

            if (hit := re_again.match(raw)) and tag == "PLAN":
                message = f"{hit.group(2).strip()}，循环 {int(hit.group(1))} 次"
            else:
                message = raw

            func = self.mind_chat

            match tag:
                case "CHAT": func = self.mind_chat
                case "PLAN": func = self.mind_plan
                case "FAST": func = self.mind_chat

            await self.calling(model, apikey, message=message, func=func)

    async def calling(self, model: str = None, apikey: str = None, *, message: str, func: typing.Callable) -> None:
        model  = model  or self.pref.model
        apikey = apikey or self.pref.apikey

        try:
            return await func(model, apikey, message)

        except* (httpx.ConnectError, httpx.ProxyError, httpx.TimeoutException) as eg:
            await self.off_live_state()
            for e in eg.exceptions:
                logger.error(f"❌ [NET] {type(e).__name__}: {e!r}")

        except* httpx.HTTPStatusError as eg:
            await self.off_live_state()
            for e in eg.exceptions:
                body = e.response.extensions.get("error_body", b"")
                text = body.decode(const.CHARSET, errors="replace")
                logger.error(f"❌ [HTTP] {e.response.status_code} {text}")

        except* Exception as eg:
            await self.off_live_state()
            for e in eg.exceptions:
                logger.error(f"❌ [BUG] {type(e).__name__}: {e}")


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
    await Design.particle_aggregate()

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
    Active.active(level := "DEBUG" if cmd_lines.debug else "INFO")

    pref_file = os.path.join(initial_source, const.SRC_OPERA_PLACE, const.PREF)
    pref = Preferences(pref_file)

    if cmd_lines.pref:
        return await pref.view_perf()

    # Notes: ========== 授权流程 ==========
    # lic_file = Path(src_opera_place) / const.LIC_FILE
    #
    # if apply_code := cmd_lines.apply:
    #     return await authorize.receive_license(apply_code, lic_file)
    #
    # await authorize.verify_license(lic_file)

    # Notes: ========== 工具路径设置 ==========
    if platform == "win32":
        supports = os.path.join(turbo, "Windows").format()
        helix = os.path.join(supports, "helix.dist", "helix.exe")
    elif platform == "darwin":
        supports = os.path.join(turbo, "MacOS").format()
        helix = os.path.join(supports, "helix.app", "Contents", "MacOS", "helix")
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
    global_config_task = asyncio.create_task(Api.remote_config())

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

    await pref.load_pref()
    await privileged()

    server: ServerManage = ServerManage()

    # ========== 本地调试 ==========
    root = Path(__file__).parent
    helix = str(root / "backend" / "helix.py")

    if helix.endswith("py"):
        await server.mcp_begin([sys.executable, helix, "--level", level])
    else:
        await server.mcp_begin([helix, "--level", level])

    positions = (
        cmd_lines.chat, cmd_lines.plan, cmd_lines.fast, cmd_lines.debug
    )
    keywords = {
        "pref": pref
    }
    remote = await global_config_task

    mind = Mind(wires, level, power, remote, *positions, **keywords)

    signal.signal(signal.SIGINT, mind.signal_processor)

    try:
        if chat := cmd_lines.chat:
            await mind.calling(message=chat, func=mind.mind_chat)
        elif plan := cmd_lines.plan:
            await mind.calling(message=plan, func=mind.mind_plan)
        elif fast := cmd_lines.fast:
            await mind.calling(message=fast, func=mind.mind_chat)
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
