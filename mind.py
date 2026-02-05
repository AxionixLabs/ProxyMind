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
import signal
import typing
import asyncio

# ====[ from: 内置模块 ]====
from pathlib import Path

# ====[ from: 第三方库 ]====
from loguru import logger
from rich.prompt import Prompt
from mcp import (
    ClientSession, ListToolsResult
)
from mcp.types import CallToolResult
from mcp.client.streamable_http import streamable_http_client

# ====[ from: 本地模块 ]====
from mindcore.api import Api
from mindcore.design import Design, TypewriterStreamSession
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

        self.base_url: str = "http://127.0.0.1:3333"

        _ = args

        self.src_opera_place: str = kwargs["src_opera_place"]
        self.src_total_place: str = kwargs["src_total_place"]
        self.pref: Preferences    = kwargs["pref"]

        self.task_event: asyncio.Event = asyncio.Event()
        self.task_info: list = []

        self.stream_event: typing.Optional[asyncio.Event] = None
        self.stream_task: typing.Optional[asyncio.Task] = None

        self.plan_event: typing.Optional[asyncio.Event] = None
        self.plan_task: typing.Optional[asyncio.Task] = None

        self.last_refresh_ts = 0.0
        self.ttl_sec         = 1.0

        self.sse: typing.Callable[
            [dict], str
        ] = lambda x: f"data: {json.dumps(x, ensure_ascii=False)}\n\n"

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
        sys.exit(130)

    async def stop_stream(self) -> None:
        """Stop Stream"""
        self.stream_event and self.stream_event.set()
        self.stream_task and await self.stream_task

    async def stop_plan(self) -> None:
        """Stop Plan"""
        self.plan_event and self.plan_event.set()
        self.plan_task and await self.plan_task

    async def stop_all(self) -> None:
        """Stop All"""
        await self.stop_stream()
        await self.stop_plan()

    @staticmethod
    def fields(result: CallToolResult) -> typing.Union[dict[str, typing.Any], str]:
        return sc if (sc := result.structuredContent) else result.content[0].text

    @staticmethod
    def ensure_model_key(model: str, apikey: str) -> None:
        if model and apikey:
            return None

        missing = ", ".join(
            x for x, ok in [("model", bool(model)), ("api_key", bool(apikey))] if not ok
        )
        raise MindError(f"Missing required field(s): {missing}")

    @staticmethod
    def build_openai_tools(list_tools: ListToolsResult) -> list[dict[str, typing.Any]]:
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
            if not (getattr(tool, "meta", None) or {}).get("hide", False)
        ]

        logger.debug(f"Tool [{len(openai_tools)}]")
        for tool in openai_tools:
            logger.debug(f"Tool {tool['function']['name']}")

        return openai_tools

    async def with_mcp_session(
        self,
        model: str,
        apikey: str,
        function: typing.Callable[[ClientSession, list[dict[str, typing.Any]]], typing.Awaitable[None]]
    ) -> None:
        """With MCP Session"""

        self.ensure_model_key(model, apikey)

        url = self.base_url + "/helix/mcp"
        headers = {"Authorization": f"Bearer {authentic.manufacture_token()}"}
        timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
        event_hooks = {"response": [request.capture]}

        async with httpx.AsyncClient(headers=headers, timeout=timeout, event_hooks=event_hooks) as client:
            async with streamable_http_client(url, http_client=client) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    list_tools = await session.list_tools()
                    openai_tools = self.build_openai_tools(list_tools)
                    await function(session, openai_tools)

    # Notes: ==== Chat 对话模式 ====
    async def chat_exec_looper(
        self,
        session: ClientSession,
        model: str,
        apikey: str,
        message: str,
        openai_tools: list[dict]
    ) -> None:
        """Chat Exec Looper"""

        async with TypewriterStreamSession() as tw:
            # workflow: ==== Chat Streaming ====
            async for chat in request.stream_chat(model, apikey, message, openai_tools):
                # await self.stop_stream()
                match chat.get("type"):
                    case "error":
                        return await tw.feed(chat.get("content"))
                    case "chat":
                        await tw.feed(chat.get("content"))
                        continue
                    case "tool_call":
                        name, arguments = chat["name"], chat.get("arguments", {})
                        await tw.feed(f"\n{name} {arguments}\n")

                        result = await session.call_tool(name, arguments)
                        ok = (not result.isError)

                        enhancer: Enhancer = Enhancer(name, arguments, result, ok, session)
                        fields = await enhancer.enhance()

                        if self.level != const.SHOW_LEVEL:
                            await tw.feed(f"\n{fields}\n")

                        await request.post_tool_result(
                            chat["cid"], chat["sid"], chat["call_id"], name, ok, fields
                        )
                        continue
                    case "tool_result":
                        if self.level != const.SHOW_LEVEL:
                            await tw.feed(f"\n{chat['name']} ok={chat.get('ok')}\n")
                        continue
                    case _:
                        continue

    # Notes: ==== Plan 编排模式 ====
    async def plan_exec_status(self, session: ClientSession) -> typing.Optional[str]:
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

    # Notes: ==== Plan 编排模式 ====
    async def plan_exec_looper(
        self,
        session: ClientSession,
        model: str,
        apikey: str,
        message: str,
        openai_tools: list[dict]
    ) -> None:
        """Plan Exec Looper"""

        async for plan in request.stream_plan(model, apikey, message, openai_tools):
            # await self.stop_stream()
            if plan.get("type") == "error":
                return logger.error(plan)

            steps, loop_count, reasoning = plan["steps"], plan["loop_count"], plan["reasoning"]

            logger.debug(reasoning)
            for index, _ in enumerate(range(loop_count), start=1):
                for step in steps:
                    action = step["action"]

                    # workflow: ==== 设备缓存 ====
                    if error := await self.plan_exec_status(session):
                        return logger.error(error)

                    name, arguments = action["action"], action["args"]
                    logger.debug(f"{name} -> args={arguments}")

                    # workflow: ==== 工具调用 ====
                    result = await session.call_tool(name, arguments)
                    fields = self.fields(result)
                    if result.isError:
                        return logger.error(fields)

                    # workflow: ==== 查找指令 ====
                    if name in {"find_element"} and (elements := fields.get("results")):
                        # serial -> {by,value}
                        locator_map: dict[str, dict[str, str]] = {}  
                        for element in elements:
                            serial = element["data"].pop("serial", None)
                            async for heal in request.stream_heal(model, apikey, **element["data"]):
                                if heal.get("type") == "error":
                                    return logger.error(heal["content"])

                                if smart := heal.get("smart"):
                                    loc = {
                                        "by": smart["new_selector"]["primary"]["by"],
                                        "value": smart["new_selector"]["primary"]["value"],
                                    }
                                    if serial in locator_map:
                                        logger.debug(f"[SKIP] dup locator for {serial}: {loc}")
                                    else:
                                        locator_map[serial] = loc
                                    logger.debug(smart)

                        if locator_map and arguments.get("should_click"):
                            if waiting := arguments.get("wait", 0):
                                await asyncio.sleep(waiting)

                            r = await session.call_tool("click_matrix", {"matrix": locator_map})
                            fields = self.fields(r)
                            if r.isError: return logger.error(fields)
                            logger.debug(fields)

                    # workflow: ==== 常规指令 ====
                    else:
                        logger.debug(f"{name} -> resp={fields}")

                if index != loop_count: self.task_info.clear()

    # workflow: ==== Chat 对话模式 ====
    async def mind_chat(self, model: str, apikey: str, message: str) -> None:
        """Mind Chat"""
        async def function(session: ClientSession, openai_tools: list[dict]) -> None:
            await self.chat_exec_looper(session, model, apikey, message, openai_tools)

        return await self.with_mcp_session(model, apikey, function)

    # workflow: ==== Plan 编排模式 ====
    async def mind_plan(self, model: str, apikey: str, message: str) -> None:
        """Mind Plan"""
        async def function(session: ClientSession, openai_tools: list[dict]) -> None:
            await self.plan_exec_looper(session, model, apikey, message, openai_tools)

        return await self.with_mcp_session(model, apikey, function)

    # workflow: ==== Loop 循环模式 ====
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
        [bold #FFD75F]/chat[/]                     对话模式（自由对话）
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
                    self.task_event.set()
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
        """Calling"""
        def flatten_exceptions(exc: BaseException) -> typing.Generator[BaseException, None, None]:
            if isinstance(exc, BaseExceptionGroup):
                for sub in exc.exceptions: yield from flatten_exceptions(sub)
            else:
                yield exc

        model  = model  or self.pref.model
        apikey = apikey or self.pref.apikey

        # self.stream_event = asyncio.Event()
        # self.stream_task = asyncio.create_task(
        #     self.design.prefix_line(self.stream_event)
        # )

        try:
            return await func(model, apikey, message)

        except* (httpx.ConnectError, httpx.ProxyError, httpx.TimeoutException) as eg:
            await self.stop_all()
            for ex in flatten_exceptions(eg):
                logger.error(f"❌ [NET] {ex!r}")

        except* httpx.HTTPStatusError as eg:
            await self.stop_all()
            for ex in flatten_exceptions(eg):
                if isinstance(ex, httpx.HTTPStatusError):
                    body = ex.response.extensions.get("error_body", b"")
                    text = body.decode(const.CHARSET, errors="replace")
                    logger.error(f"❌ [HTTP] {ex.response.status_code} {text}")
                else:
                    logger.error(f"❌ [HTTP] unexpected: {ex!r}")

        except* Exception as eg:
            await self.stop_all()
            for ex in flatten_exceptions(eg):
                logger.error(f"❌ [BUG] {ex!r}")


class Enhancer(object):
    """通用工具结果增强"""

    def __init__(
        self,
        name: str,
        arguments: dict[str, typing.Any],
        result: CallToolResult,
        ok: bool,
        session: ClientSession
    ):
        self.name      = name
        self.arguments = arguments
        self.result    = result
        self.ok        = ok
        self.session   = session

    @staticmethod
    def fields(result: CallToolResult) -> typing.Union[dict[str, typing.Any], str]:
        return sc if (sc := result.structuredContent) else result.content[0].text

    async def enhance(self) -> typing.Union[str, dict[str, typing.Any]]:
        content = self.result.content[0].text
        if not self.ok: return content

        match self.name:
            case "screenshot":
                return await self.__enhance_screenshot()
            case _:
                return content

    async def __enhance_screenshot(self) -> dict:
        fields = self.result.structuredContent

        attachments: list[dict[str, typing.Any]] = []
        per_device: dict[str, typing.Any] = {}

        for item in fields.get("results") or []:
            agent_id = item.get("agent_id") or "unknown"

            if not item.get("ok"):
                per_device[agent_id] = {"ok": False, "error": item.get("text")}
                continue

            if not (local := item.get("text")) or not isinstance(local, str):
                per_device[agent_id] = {"ok": False, "error": "missing local screenshot path"}
                continue

            try:
                up = await request.upload_file_stream(local, agent_id, "screenshots")
                if not (url := up.get("url")):
                    per_device[agent_id] = {"ok": False, "error": f"upload returned no url: {up!r}"}
                    continue

                attachments.append({"kind": "image", "url": url, "agent_id": agent_id})
                per_device[agent_id] = {
                    "ok"        : True,
                    "local"     : local,
                    "url"       : url,
                    "r2_key"    : up["key"],
                    "filename"  : up["filename"],
                    "mime_type" : up["mime_type"]
                }
            finally:
                if os.path.exists(local): os.remove(local)

        return {
            "text"        : "screenshot uploaded",
            "attachments" : attachments,
            "data"        : {"per_device": per_device}
        }


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

    if not os.path.exists(
        src_total_place := os.path.join(initial_source, const.SRC_TOTAL_PLACE).format()
    ):
        os.makedirs(src_total_place, exist_ok=True)

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

    # ========== 本地调试 ==========
    helix = str(Path(__file__).parent / "backend" / "helix.py")

    if helix.endswith("py"):
        cmd = [sys.executable, helix, "--level", level]
    else:
        cmd = [helix, "--level", level]

    server: ServerManage = ServerManage(cmd=cmd, base_url="http://127.0.0.1:3333")
    await server.ensure_running()

    positions = (
        cmd_lines.chat, cmd_lines.plan, cmd_lines.fast, cmd_lines.debug
    )
    keywords = {
        "src_opera_place" : src_opera_place,
        "src_total_place" : src_total_place,
        "pref"            : pref
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
        await server.aclose()


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
