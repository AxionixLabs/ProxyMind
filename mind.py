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
from rich.prompt import Prompt
from mcp import (
    ClientSession, ListToolsResult
)
from mcp.client.streamable_http import streamable_http_client

# ====[ from: 本地模块 ]====
from mindcore.api import Api
from mindcore.design import Design
from engine.enhancer import Enhancer
from engine.manage import ServerManage
from engine.scaling import Pack
from engine.tinker import (
    MindError, Active, Tooling, StreamTyperLogger
)
from engine.terminal import Terminal
from mindcore import authorize
from mindcore.parser import Parser
from mindcore.profile import Preferences
from mindnova.report import Report
from mindnova.request import EventReport
from mindnova import (
    authentic, const, craft, request
)


class Mind(object):
    """Mind class."""

    __remote: dict = {}

    def __init__(self, wires: list, level: str, power: int, remote: dict, *args, **kwargs):
        self.wires = wires  # 命令参数
        self.level = level  # 日志级别
        self.power = power  # 最大进程

        self.remote: dict = remote or {}  # workflow: 远程全局配置

        *_, self.gravity, self.reflection, self.repeat, self.pattern = args

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

        self.cid: typing.Optional[str] = None
        self.sid: typing.Optional[str] = None

        self.report: Report = Report(self.src_total_place, self.gravity)

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
        self.task_event.set()
        Design.console.print()
        Design.show_exit()
        sys.exit(130)

    async def stop_stream_anim(self) -> None:
        """Stop Stream"""
        self.stream_event and self.stream_event.set()
        self.stream_task and await self.stream_task

    async def stop_plan_anim(self) -> None:
        """Stop Plan"""
        self.plan_event and self.plan_event.set()
        self.plan_task and await self.plan_task

    async def stop_all_anim(self) -> None:
        """Stop All"""
        await self.stop_stream_anim()
        await self.stop_plan_anim()

    @staticmethod
    def ensure_model_key(model: str, apikey: str) -> None:
        if model and apikey:
            return None

        missing = ", ".join(
            x for x, ok in [("model", bool(model)), ("api_key", bool(apikey))] if not ok
        )
        raise MindError(f"Missing required field(s): {missing}")

    @staticmethod
    def build_openai_tools(
        list_tools: ListToolsResult
    ) -> tuple[list[dict[str, typing.Any]], dict[str, dict[str, typing.Any]]]:
        """Build OpenAI Tools"""
        openai_tools: list[dict[str, typing.Any]] = []
        domains: dict[str, dict[str, typing.Any]] = {}

        for tool in list_tools.tools:
            if bool((meta := tool.meta).get("hidden", False)):
                continue
            domains[tool.name] = meta

            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description" : tool.description,
                    "parameters"  : tool.inputSchema
                }
            })

        logger.debug(f"Tool [{len(openai_tools)}]")
        for tool in openai_tools:
            logger.debug(f"Tool {tool['function']['name']}")

        return openai_tools, domains

    def begin_session(
        self,
        cid: typing.Optional[str] = None,
        sid: typing.Optional[str] = None
    ) -> dict[str, str]:
        """Begin Session"""
        self.cid = cid or self.cid or craft.new_cid()
        self.sid = sid or self.sid or craft.new_sid(self.cid)
        return {"cid": self.cid, "sid": self.sid}

    async def with_mcp_session(
        self,
        model: str,
        apikey: str,
        function: typing.Callable[
            [ClientSession, list[dict[str, typing.Any]], dict[str, dict[str, typing.Any]]], typing.Awaitable[None]
        ]
    ) -> None:
        """With MCP Session"""

        async def inject_auth(req: httpx.Request) -> None:
            """Inject Auth"""
            now = int(time.time())
            if not token_cache["val"] or now - token_cache["ts"] >= 60:
                token_cache["val"] = authentic.manufacture_token()
                token_cache["ts"] = now
            req.headers["Authorization"] = f"Bearer {token_cache['val']}"

        self.ensure_model_key(model, apikey)

        url = const.BASE_URL + const.MCP_ED
        token_cache = {"ts": 0, "val": ""}
        timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)

        event_hooks = {
            "request": [inject_auth], "response": [request.cap_response]
        }

        async with httpx.AsyncClient(timeout=timeout, event_hooks=event_hooks) as client:
            async with streamable_http_client(url, http_client=client) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    list_tools = await session.list_tools()
                    openai_tools, domains = self.build_openai_tools(list_tools)
                    await function(session, openai_tools, domains)

    async def wakeup(
        self,
        session: ClientSession,
        slog: typing.Optional[StreamTyperLogger] = None
    ) -> typing.Optional[str]:
        """Wakeup"""

        if ((now := time.time()) - self.last_refresh_ts) < self.ttl_sec:
            tip = f"ttl-hit: skip refresh ttl={self.ttl_sec:.3f}s"
            if slog and self.level != const.SHOW_LEVEL:
                return await slog.feed(f"\n{tip}\n")
            else:
                return logger.debug(tip)

        result = await session.call_tool("refresh", {"ttl_sec": self.ttl_sec})
        ok = (not result.isError)
        content = result.content[0].text

        if not ok:
            return content

        self.last_refresh_ts = now

        if slog and self.level != const.SHOW_LEVEL:
            return await slog.feed(f"\n{content}\n")
        else:
            return logger.debug(content)

    async def calling(
        self,
        model: str = None,
        apikey: str = None,
        *,
        message: str,
        func: typing.Callable,
        **kwargs
    ) -> None:
        """Calling"""

        def flatten_exceptions(exc: BaseException) -> typing.Generator[BaseException, None, None]:
            if isinstance(exc, BaseExceptionGroup):
                for sub in exc.exceptions: yield from flatten_exceptions(sub)
            else:
                yield exc

        model  = model  or self.pref.model
        apikey = apikey or self.pref.apikey

        meta_in = kwargs.get("metadata") or {}
        cid = meta_in.get("cid") if isinstance(meta_in, dict) else None
        sid = meta_in.get("sid") if isinstance(meta_in, dict) else None
        kwargs["metadata"] = self.begin_session(cid=cid, sid=sid)

        self.stream_event = asyncio.Event()
        self.stream_task = asyncio.create_task(
            self.design.prefix_line(self.stream_event)
        )

        try:
            return await func(model, apikey, message, **kwargs)

        except* (httpx.ConnectError, httpx.ProxyError, httpx.TimeoutException) as eg:
            await self.stop_all_anim()
            for ex in flatten_exceptions(eg):
                logger.error(f"❌ [NET] {ex!r}")

        except* httpx.HTTPStatusError as eg:
            await self.stop_all_anim()
            for ex in flatten_exceptions(eg):
                if isinstance(ex, httpx.HTTPStatusError):
                    body = ex.response.extensions.get("error_body", b"")
                    text = body.decode(const.CHARSET, errors="replace")
                    logger.error(f"❌ [HTTP] {ex.response.status_code} {text}")
                else:
                    logger.error(f"❌ [HTTP] unexpected: {ex!r}")

        except* Exception as eg:
            await self.stop_all_anim()
            for ex in flatten_exceptions(eg):
                logger.error(f"❌ [ERROR] {ex!r}")

    # workflow: ==== Chat 对话模式 ====
    async def chat_exec_looper(
        self,
        session: ClientSession,
        model: str,
        apikey: str,
        message: str,
        openai_tools: list[dict[str, typing.Any]],
        domains: dict[str, dict[str, typing.Any]],
        *_,
        **kwargs
    ) -> None:
        """Chat Exec Looper"""

        ev_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)

        async def finish(phase: str, **extra) -> None:
            """统一收尾：先 emit，再 flush（确保返回前事件到达服务端）"""
            if not ev_report: return None
            ev_report.emit({
                "type"  : "lifecycle",
                "scope" : "chat",
                "phase" : phase,
                "ts"    : time.time(),
                **extra
            })
            await ev_report.flush()

        mode: str = "chat"

        slog: StreamTyperLogger = StreamTyperLogger(self.report.log_papers)
        await slog.open()

        # workflow: ==== Chat Streaming ====
        async for chat in request.stream_chat(mode, model, apikey, message, openai_tools, **kwargs):
            await self.stop_stream_anim(); await slog.start()

            try:
                match chat.get("type"):
                    case "error":
                        await slog.feed(chat.get("content"))
                        await finish("fail", error=chat.get("content"))
                        return await slog.stop()

                    case "chat":
                        await slog.feed(chat.get("content"))
                        continue

                    case "tool_call":
                        name, arguments = chat["name"], chat.get("arguments", {})

                        if Tooling.require(domains, name, name_not_in={"refresh"}):
                            if error := await self.wakeup(session, slog):
                                await slog.feed(error)
                                await finish("fail", error=str(error))
                                return await slog.stop()

                        await slog.feed(f"\n{name} {arguments}\n")

                        # workflow: ==== 参数增强 ====
                        dst = {"local": str(Path(self.report.cap_path) / "screenshot.png")}
                        arguments = Enhancer.exchange(name, arguments, dst)

                        # workflow: ==== 工具调用 ====
                        result = await session.call_tool(name, arguments)
                        ok = (not result.isError)

                        # workflow: ==== 工具增强 ====
                        enhancer: Enhancer = Enhancer(session, model, apikey)
                        fields = await enhancer.enhance(name, arguments, result, ok, slog)

                        await slog.feed(f"\n{fields.get('text')}\n")

                        await request.post_tool_result(
                            chat["cid"], chat["sid"], chat["call_id"], name, ok, fields
                        )

                        continue

                    case "tool_result":
                        await slog.feed(f"\n{chat['name']} ok={chat.get('ok')}\n")
                        continue

                    case _:
                        continue

            except Exception as e:
                await slog.feed(str(e))
                await finish("fail", error=f"{type(e).__name__}: {e}")
                return await slog.stop()

        await finish("done")
        await slog.stop()

    # workflow: ==== Fast 性能模式 ====
    async def fast_exec_looper(
        self,
        session: ClientSession,
        model: str,
        apikey: str,
        message: str,
        openai_tools: list[dict[str, typing.Any]],
        domains: dict[str, dict[str, typing.Any]],
        *_,
        **kwargs
    ) -> None:
        """Fast Exec Looper"""

        ev_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)

        async def finish(phase: str, **extra) -> None:
            """统一收尾：先 emit，再 flush（确保返回前事件到达服务端）"""
            if not ev_report: return None
            ev_report.emit({
                "type"  : "lifecycle",
                "scope" : "chat",
                "phase" : phase,
                "ts"    : time.time(),
                **extra
            })
            await ev_report.flush()

        mode: str = "fast"

        slog: StreamTyperLogger = StreamTyperLogger(self.report.log_papers)
        await slog.open()

        filter_tools = Tooling.filter_tools(
            openai_tools=openai_tools,
            tool_meta=domains,
            domains={"bench", "common", "media"},
            exclude=[{"domain": "media", "class": "scrcpy"}]
        )

        # workflow: ==== Fast Streaming ====
        async for chat in request.stream_chat(mode, model, apikey, message, filter_tools, **kwargs):
            await self.stop_stream_anim(); await slog.start()

            try:
                match chat.get("type"):
                    case "error":
                        await slog.feed(chat.get("content"))
                        await finish("fail", error=chat.get("content"))
                        return await slog.stop()

                    case "chat":
                        await slog.feed(chat.get("content"))
                        continue

                    case "tool_call":
                        name, arguments = chat["name"], chat.get("arguments", {})

                        await slog.feed(f"\n{name} {arguments}\n")

                        # workflow: ==== 参数增强 ====
                        dst = {"local": str(Path(self.report.cap_path) / "screenshot.png")}
                        arguments = Enhancer.exchange(name, arguments, dst)

                        # workflow: ==== 工具调用 ====
                        result = await session.call_tool(name, arguments)
                        ok = (not result.isError)

                        # workflow: ==== 工具增强 ====
                        enhancer: Enhancer = Enhancer(session, model, apikey)
                        fields = await enhancer.enhance(name, arguments, result, ok, slog)

                        await slog.feed(f"\n{fields.get('text')}\n")

                        await request.post_tool_result(
                            chat["cid"], chat["sid"], chat["call_id"], name, ok, fields
                        )
                        continue

                    case "tool_result":
                        await slog.feed(f"\n{chat['name']} ok={chat.get('ok')}\n")
                        continue

                    case _:
                        continue

            except Exception as e:
                await slog.feed(str(e))
                await finish("fail", error=f"{type(e).__name__}: {e}")
                return await slog.stop()

        await finish("done")
        await slog.stop()

    # workflow: ==== Plan 编排模式 ====
    async def plan_exec_looper(
        self,
        session: ClientSession,
        model: str,
        apikey: str,
        message: str,
        openai_tools: list[dict[str, typing.Any]],
        domains: dict[str, dict[str, typing.Any]],
        *_,
        **kwargs
    ) -> None:
        """Plan Exec Looper"""

        ev_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)

        def emit(ev: dict[str, typing.Any]) -> None:
            if ev_report: ev_report.emit(ev)

        mode: str = "plan"

        filter_tools = Tooling.filter_tools(
            openai_tools=openai_tools,
            tool_meta=domains,
            exclude=[{"domain": "common", "class": "runtime", "name": "loop_steps"}]
        )

        r = await session.call_tool("refresh", {"ttl_sec": self.ttl_sec})
        extras = None if r.isError else {"devices": r.content[0].text}

        emit({
            "type"  : "lifecycle",
            "scope" : "plan",
            "phase" : "start",
            "mode"  : mode,
            "ts"    : time.time()
        })

        async for plan in request.stream_plan(mode, model, apikey, message, filter_tools, extras, **kwargs):
            await self.stop_stream_anim()
            if plan.get("type") == "error":
                emit({
                    "type"  : "lifecycle",
                    "scope" : "plan",
                    "phase" : "fail",
                    "error" : json.dumps(plan, ensure_ascii=False),
                    "ts"    : time.time()
                })
                return logger.error(plan)

            steps, loop_count, reasoning = plan["steps"], plan["loop_count"], plan["reasoning"]

            logger.info(reasoning)

            emit({
                "type"       : "lifecycle",
                "scope"      : "plan",
                "phase"      : "ready",
                "loop_count" : loop_count,
                "steps"      : len(steps),
                "ts"         : time.time()
            })

            for index, _ in enumerate(range(loop_count), start=1):
                emit({
                    "type"  : "lifecycle",
                    "scope" : "loop",
                    "phase" : "start",
                    "run"   : index,
                    "total" : loop_count,
                    "ts"    : time.time()
                })
                for step_idx, step in enumerate(steps, start=1):
                    action = step["action"]
                    name, arguments = action["action"], action["args"]

                    emit({
                        "type"  : "lifecycle",
                        "scope" : "step",
                        "phase" : "start",
                        "run"   : index,
                        "index" : step_idx,
                        "total" : len(steps),
                        "name"  : name,
                        "args"  : arguments,
                        "ts"    : time.time()
                    })

                    if Tooling.require(domains, name, name_not_in={"refresh"}):
                        if error := await self.wakeup(session):
                            emit({
                                "type"  : "lifecycle",
                                "scope" : "step",
                                "phase" : "fail",
                                "run"   : index,
                                "index" : step_idx,
                                "total" : len(steps),
                                "name"  : name,
                                "error" : str(error),
                                "ts"    : time.time()
                            })
                            return logger.error(error)

                    logger.info(f"{name} -> args={arguments}")

                    # workflow: ==== 参数增强 ====
                    dst = {"local": str(Path(self.report.cap_path) / "screenshot.png")}
                    arguments = Enhancer.exchange(name, arguments, dst)

                    t0 = time.time()

                    # workflow: ==== 工具调用 ====
                    result = await session.call_tool(name, arguments)
                    ok = (not result.isError)

                    # workflow: ==== 工具增强 ====
                    enhancer: Enhancer = Enhancer(session, model, apikey)
                    fields = await enhancer.enhance(name, arguments, result, ok)

                    data_ok = bool((fields or {}).get("data", {}).get("ok"))
                    if not ok or not data_ok:
                        emit({
                            "type"    : "lifecycle",
                            "scope"   : "step",
                            "phase"   : "fail",
                            "run"     : index,
                            "index"   : step_idx,
                            "total"   : len(steps),
                            "name"    : name,
                            "cost_ms" : int((time.time() - t0) * 1000),
                            "error"   : (fields.get("text") if isinstance(fields, dict) else "step failed"),
                            "ts"      : time.time()
                        })
                        return logger.error(fields)

                    logger.info(fields.get("text"))
                    emit({
                        "type"    : "lifecycle",
                        "scope"   : "step",
                        "phase"   : "done",
                        "run"     : index,
                        "index"   : step_idx,
                        "total"   : len(steps),
                        "name"    : name,
                        "cost_ms" : int((time.time() - t0) * 1000),
                        "ts"      : time.time()
                    })

                emit({
                    "type"  : "lifecycle",
                    "scope" : "loop",
                    "phase" : "done",
                    "run"   : index,
                    "total" : loop_count,
                    "ts"    : time.time()
                })

                if index != loop_count: self.task_info.clear()

            return emit({
                "type"  : "lifecycle",
                "scope" : "plan",
                "phase" : "done",
                "ts"    : time.time()
            })

    # Notes: ==== Chat 对话模式 ====
    async def mind_chat(self, model: str, apikey: str, message: str, *_, **kwargs) -> None:
        """Mind Chat"""

        async def function(
            session: ClientSession,
            openai_tools: list[dict[str, typing.Any]],
            domains: dict[str, dict[str, typing.Any]]
        ) -> None:
            """Function"""
            await self.chat_exec_looper(
                session, model, apikey, message, openai_tools, domains, **kwargs
            )

        return await self.with_mcp_session(model, apikey, function)

    # Notes: ==== Fast 性能模式 ====
    async def mind_fast(self, model: str, apikey: str, message: str, *_, **kwargs) -> None:
        """Mind Fast"""

        async def function(
            session: ClientSession,
            openai_tools: list[dict[str, typing.Any]],
            domains: dict[str, dict[str, typing.Any]]
        ) -> None:
            """Function"""
            await self.fast_exec_looper(
                session, model, apikey, message, openai_tools, domains, **kwargs
            )

        return await self.with_mcp_session(model, apikey, function)

    # Notes: ==== Plan 编排模式 ====
    async def mind_plan(self, model: str, apikey: str, message: str, *_, **kwargs) -> None:
        """Mind Plan"""

        async def function(
            session: ClientSession,
            openai_tools: list[dict[str, typing.Any]],
            domains: dict[str, dict[str, typing.Any]]
        ) -> None:
            """Function"""
            await self.plan_exec_looper(
                session, model, apikey, message, openai_tools, domains, **kwargs
            )

        return await self.with_mcp_session(model, apikey, function)

    # Notes: ==== Loop 循环模式 ====
    async def mind_loop(self) -> None:
        """Mind Loop"""

        async def exchange(types: typing.Literal["model", "apikey"]) -> typing.Optional[str]:
            """Exchange"""
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

        metadata = self.begin_session()

        quit_set: set[str] = {"/quit", "/q", "quit", "exit"}
        help_set: set[str] = {"/help", "/h"}
        seal_set: set[str] = {"/license", "/lic"}
        subs_set: set[str] = {"/subscription", "/sub"}

        doc = """\
        [bold]
        [bold #AFD7FF]/help, /h[/]                 指令索引（用法/示例/约定）
        [bold #5FD7AF]/license, /lic[/]            授权许可（License/特性）
        [bold #5FD7AF]/subscription, /sub[/]       订阅信息（授权状态/到期）
        [bold #FF5F5F]/quit, /q, quit, exit[/]     断开会话（安全退出）
        [bold #AFD7FF]/model <name>[/]             引擎切换（选择推理内核）
        [bold #AFD7FF]/apikey <key>[/]             凭证更新（替换访问密钥）
        [bold #AFD7FF]/again N <goal>[/]           复现回放（目标 × N 次）
        [bold #FFD75F]/chat[/]                     对话模式（自由对话）
        [bold #FFD75F]/fast[/]                     性能模式（压测采集）
        [bold #FFD75F]/plan[/]                     编排模式（工具执行）
        [/]"""

        re_again  = re.compile(r"^\s*/again\s+(\d+)\s+(.+?)\s*$", re.IGNORECASE)
        re_model  = re.compile(r"^\s*/model(?:\s+(.*))?\s*$", re.IGNORECASE)
        re_apikey = re.compile(r"^\s*/apikey(?:\s+(.*))?\s*$", re.IGNORECASE)

        tag: typing.Literal["CHAT", "FAST", "PLAN"] = "CHAT"

        theme = {
            "CHAT": {
                "banner"   : "╔═⟦ 𝑪𝒉𝒂𝒕 ⟧═╗",
                "prompt"   : "│ 〉Chat",
                "tag"      : "#FF87D7",
                "prompt_c" : "#FFD75F",
                "model"    : "#FFAF5F",
                "ready"    : "#D7AFFF",
                "hint"     : "#FF87D7"
            },
            "FAST": {
                "banner"   : "╔═⟦ 𝑭𝒂𝒔𝒕 ⟧═╗",
                "prompt"   : "│ 〉Fast",
                "tag"      : "#FFAF00",
                "prompt_c" : "#FFD75F",
                "model"    : "#FFAF00",
                "ready"    : "#AFD7FF",
                "hint"     : "#FFAF00"
            },
            "PLAN": {
                "banner"   : "╔═⟦ 𝑷𝒍𝒂𝒏 ⟧═╗",
                "prompt"   : "│ 〉Plan",
                "tag"      : "#5FD7FF",
                "prompt_c" : "#87FFAF",
                "model"    : "#5FFF87",
                "ready"    : "#AFD7FF",
                "hint"     : "#5FD7FF"
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
                raw = Prompt.ask(ask, console=Design.console).strip()
            except (EOFError, UnicodeDecodeError):
                continue

            if raw.lower() in quit_set:
                self.task_event.set()
                break

            if raw.lower() in help_set:
                Design.console.print(doc)
                continue

            if raw.lower() in seal_set:
                Design.startup_logo()
                continue

            if raw.lower() in subs_set:
                lic_file = Path(self.src_opera_place) / const.LIC_FILE
                await authorize.verify_license(lic_file)
                continue

            if raw.lower() == "/chat":
                tag = "CHAT"
                Design.console.print(f"[bold {theme['CHAT']['hint']}]Exchange → Chat[/]")
                continue

            if raw.lower() == "/fast":
                tag = "FAST"
                Design.console.print(f"[bold {theme['FAST']['hint']}]Exchange → Fast[/]")
                continue

            if raw.lower() == "/plan":
                tag = "PLAN"
                Design.console.print(f"[bold {theme['PLAN']['hint']}]Exchange → Plan[/]")
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
                case "FAST": func = self.mind_fast
                case "PLAN": func = self.mind_plan

            await self.calling(
                model, apikey, message=message, func=func, metadata=metadata
            )

    # Notes: ==== Pack 批量模式 ====
    async def mind_pack(self, file: str, func: typing.Callable, *_, **kwargs) -> None:
        """Mind Pack"""

        async def function(
            session: ClientSession,
            openai_tools: list[dict[str, typing.Any]],
            domains: dict[str, dict[str, typing.Any]]
        ) -> None:
            """Function"""

            async def virtual(name: str, msg: str, run: typing.Optional[int] = None) -> None:
                """Virtual"""
                if not msg.strip(): return None
                logger.info(f"🧩 {name} file={p}")
                ev_report.emit({
                    "type"  : "lifecycle",
                    "scope" : "virtual",
                    "phase" : "start",
                    "name"  : name,
                    "run"   : run,
                    "ts"    : time.time()
                })
                await func(session, model, apikey, msg, openai_tools, domains, **kwargs)
                ev_report.emit({
                    "type"  : "lifecycle",
                    "scope" : "virtual",
                    "phase" : "done",
                    "name"  : name,
                    "run"   : run,
                    "ts"    : time.time()
                })

            loop_prefix = (cfg.get("loop_prefix") or "").strip()
            loop_suffix = (cfg.get("loop_suffix") or "").strip()

            round_prefix = (cfg.get("round_prefix") or "").strip()
            round_suffix = (cfg.get("round_suffix") or "").strip()

            global_prefix = (cfg.get("global_prefix") or "").strip()
            global_suffix = (cfg.get("global_suffix") or "").strip()

            # --- loop 前置 ---
            await virtual("__loop_prefix__", loop_prefix)

            ev_report.emit({
                "type"   : "lifecycle",
                "scope"  : "batch",
                "phase"  : "start",
                "file"   : str(p),
                "items"  : len(items),
                "repeat" : repeat,
                "ts"     : time.time()
            })

            try:
                for r in range(1, repeat + 1):
                    # --- round 前置 ---
                    await virtual("__round_prefix__", round_prefix, run=r)

                    logger.info(f"🧪 run {r}/{repeat} items={len(items)} file={p}")

                    for idx, it in enumerate(items, start=1):
                        self.stream_event = asyncio.Event()
                        self.stream_task = asyncio.create_task(
                            self.design.prefix_line(self.stream_event)
                        )

                        if rx and not rx.search(it.name):
                            ev_report.emit({
                                "type"   : "lifecycle",
                                "scope"  : "task",
                                "phase"  : "skip",
                                "run"    : r,
                                "index"  : idx,
                                "total"  : len(items),
                                "name"   : it.name,
                                "reason" : "filter",
                                "ts"     : time.time()
                            })
                            logger.debug(f"⏭️  skip [{idx}/{len(items)}] {it.name} (filter)")
                            continue

                        ev_report.emit({
                            "type"  : "lifecycle",
                            "scope" : "task",
                            "phase" : "start",
                            "run"   : r,
                            "index" : idx,
                            "total" : len(items),
                            "name"  : it.name,
                            "ts"    : time.time()
                        })

                        logger.info(f"▶️  [{idx}/{len(items)}] {it.name}")

                        max_attempts: int = 3
                        last_exc: typing.Optional[BaseException] = None

                        for attempt in range(1, max_attempts + 1):
                            t0 = time.time()

                            ev_report.emit({
                                "type"         : "lifecycle",
                                "scope"        : "task",
                                "phase"        : "attempt",
                                "run"          : r,
                                "index"        : idx,
                                "total"        : len(items),
                                "name"         : it.name,
                                "attempt"      : attempt,
                                "max_attempts" : max_attempts,
                                "ts"           : time.time()
                            })

                            prefix = (it.meta.get("prefix") or global_prefix or "").strip()
                            suffix = (it.meta.get("suffix") or global_suffix or "").strip()

                            final_msg = it.message
                            if prefix:
                                final_msg = f"{prefix}\n{final_msg}"
                            if suffix:
                                final_msg = f"{final_msg}\n{suffix}"

                            rule_suffix = (it.meta.get(
                                "rule_suffix"
                            ) or it.meta.get(
                                "global_rule_suffix"
                            ) or "").strip()

                            if rule_suffix:
                                final_msg = f"{final_msg}\n\n{rule_suffix}"

                            try:
                                await func(session, model, apikey, final_msg, openai_tools, domains, **kwargs)

                                ev_report.emit({
                                    "type"    : "lifecycle",
                                    "scope"   : "task",
                                    "phase"   : "done",
                                    "run"     : r,
                                    "index"   : idx,
                                    "total"   : len(items),
                                    "name"    : it.name,
                                    "attempt" : attempt,
                                    "cost_ms" : int((time.time() - t0) * 1000),
                                    "ts"      : time.time()
                                })

                                last_exc = None
                                break

                            except BaseException as e:
                                last_exc = e
                                await self.stop_all_anim()

                                ev_report.emit({
                                    "type"         : "lifecycle",
                                    "scope"        : "task",
                                    "phase"        : "fail",
                                    "run"          : r,
                                    "index"        : idx,
                                    "total"        : len(items),
                                    "name"         : it.name,
                                    "attempt"      : attempt,
                                    "max_attempts" : max_attempts,
                                    "error"        : Pack.brief_err(e),
                                    "ts"           : time.time()
                                })

                                logger.error(
                                    f"❌ item failed: {it.name} "
                                    f"attempt={attempt}/{max_attempts} err={Pack.brief_err(e)}\n"
                                )

                                if attempt < max_attempts:
                                    backoff = 0.5 * (2 ** (attempt - 1))  # 0.5s, 1.0s
                                    ev_report.emit({
                                        "type"    : "lifecycle",
                                        "scope"   : "task",
                                        "phase"   : "retry_wait",
                                        "run"     : r,
                                        "index"   : idx,
                                        "total"   : len(items),
                                        "name"    : it.name,
                                        "attempt" : attempt,
                                        "wait_s"  : backoff,
                                        "ts"      : time.time()
                                    })
                                    await asyncio.sleep(backoff)

                        if last_exc is not None:
                            ev_report.emit({
                                "type"         : "lifecycle",
                                "scope"        : "task",
                                "phase"        : "give_up",
                                "run"          : r,
                                "index"        : idx,
                                "total"        : len(items),
                                "name"         : it.name,
                                "max_attempts" : max_attempts,
                                "error"        : Pack.brief_err(last_exc),
                                "ts"           : time.time()
                            })
                            logger.error(
                                f"🧯 give up: {it.name} attempts={max_attempts} last={Pack.brief_err(last_exc)}"
                            )
                            continue

                    # --- round 后置 ---
                    await virtual("__round_suffix__", round_suffix, run=r)

                # --- loop 后置 ---
                await virtual("__loop_suffix__", loop_suffix)

            finally:
                ev_report.emit({
                    "type"   : "lifecycle",
                    "scope"  : "batch",
                    "phase"  : "done",
                    "file"   : str(p),
                    "items"  : len(items),
                    "repeat" : repeat,
                    "ts"     : time.time()
                })

                await ev_report.flush()
                await ev_report.close()

        if not (p := Path(file).expanduser()).exists():
            raise MindError(f"File not found: {p}")

        text = p.read_text(encoding=const.CHARSET, errors="replace")

        items, cfg = Pack.pack_parse(text)
        if not items:
            logger.warning("Pack has no cases; will run only loop/round hooks.")

        repeat = max(1, int(self.repeat or 1))
        rx     = re.compile(self.pattern) if self.pattern else None

        model  = self.pref.model
        apikey = self.pref.apikey

        meta_in = kwargs.get("metadata") or {}
        cid = meta_in.get("cid") if isinstance(meta_in, dict) else None
        sid = meta_in.get("sid") if isinstance(meta_in, dict) else None
        kwargs["metadata"] = meta = self.begin_session(cid=cid, sid=sid)

        ev_report: EventReport = EventReport(meta["cid"], meta["sid"])
        kwargs["ev_report"] = ev_report
        await ev_report.open()

        return await self.with_mcp_session(model, apikey, function)


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
    Active.active(level := "DEBUG" if cmd_lines.reflection else "INFO")

    pref_file = os.path.join(initial_source, const.SRC_OPERA_PLACE, const.PREF)
    pref = Preferences(pref_file)

    if cmd_lines.pref:
        return await pref.view_perf()

    # Notes: ========== 授权流程 ==========
    lic_file = Path(src_opera_place) / const.LIC_FILE

    if apply_code := cmd_lines.apply:
        return await authorize.receive_license(apply_code, lic_file)

    await authorize.verify_license(lic_file)

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
    for tls in tools:
        if not shutil.which((tls_name := os.path.basename(tls))):
            raise MindError(f"{const.APP_DESC} missing files {tls_name}")

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

    launch_cmd = [helix, "--level", level]
    server: ServerManage = ServerManage(launch_cmd)
    await server.ensure_running()
    await server.close()

    positions = (
        cmd_lines.chat, cmd_lines.plan, cmd_lines.fast, cmd_lines.file,
        cmd_lines.gravity, cmd_lines.reflection, cmd_lines.repeat, cmd_lines.pattern
    )
    keywords = {
        "src_opera_place" : src_opera_place,
        "src_total_place" : src_total_place,
        "pref"            : pref
    }
    remote = await global_config_task

    mind = Mind(wires, level, power, remote, *positions, **keywords)

    signal.signal(signal.SIGINT, mind.signal_processor)

    if chat := cmd_lines.chat:
        await mind.calling(message=chat, func=mind.mind_chat)
    elif plan := cmd_lines.plan:
        await mind.calling(message=plan, func=mind.mind_plan)
    elif fast := cmd_lines.fast:
        await mind.calling(message=fast, func=mind.mind_fast)
    elif file := cmd_lines.file:
        func = (
            mind.chat_exec_looper if cmd_lines.chat is not None else
            mind.fast_exec_looper if cmd_lines.fast is not None else
            mind.plan_exec_looper
        )

        await mind.mind_pack(file, func)

    else:
        await mind.mind_loop()


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
