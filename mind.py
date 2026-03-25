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
import shutil
import signal
import typing
import asyncio
import contextlib

# ====[ from: 内置模块 ]====
from pathlib import Path

# ====[ from: 第三方库 ]====
from loguru import logger
from mcp import (
    ClientSession, ListToolsResult
)
from mcp.client.streamable_http import streamable_http_client

# ====[ from: 本地模块 ]====
from mindcore.api import Api
from mindcore.design import Design
from mindcore.prompting import PromptToolkitBox
from engine.enhancer import Enhancer
from engine.manage import ServerManage
from engine.animaion import AsyncAnimManager
from engine.scaling import (
    PackItem, Pack
)
from engine.tinker import (
    MindError, Active, Tooling, StreamTyperLogger, FileAssist
)
from engine.terminal import Terminal
from engine.upgrade import Upgrade
from mindcore import authorize
from mindcore.parser import Parser
from mindcore.preference import Preferences
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

        *_, self.gravity, self.reflection, _ = args

        self.src_opera_place: str = kwargs["src_opera_place"]
        self.src_total_place: str = kwargs["src_total_place"]
        self.pref: Preferences    = kwargs["pref"]

        self.task_event: asyncio.Event = asyncio.Event()
        self.task_info: list = []

        self.anim_manager: AsyncAnimManager = AsyncAnimManager()

        self.last_refresh_ts: float = 0.0
        self.ttl_sec: float         = 1.0

        self.sse: typing.Callable[
            [dict], str
        ] = lambda x: f"data: {json.dumps(x, ensure_ascii=False)}\n\n"

        self.design: Design = Design(self.level)

        self.cid: typing.Optional[str] = None
        self.sid: typing.Optional[str] = None

        self.report: Report = Report(self.src_total_place, self.gravity)

        self.prompt_box: PromptToolkitBox = PromptToolkitBox()

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

    async def stop_anim(self) -> None:
        """Stop Anim"""
        await self.anim_manager.stop()

    async def start_anim(self, mode: typing.Literal["chat", "fast", "plan"] = "chat") -> None:
        """Start Anim"""
        await self.anim_manager.start(lambda stop_event: self.design.stream_wait_live(stop_event, mode))

    @staticmethod
    def ensure_model_api(model_api: dict[str, typing.Any]) -> None:
        """Ensure Model Key"""
        primary = model_api.get("primary") if isinstance(model_api, dict) else None
        if isinstance(primary, dict):
            api    = primary.get("api")
            model  = primary.get("model")
            apikey = primary.get("apikey")
        else:
            api    = model_api["api"]
            model  = model_api["model"]
            apikey = model_api["apikey"]

        if api and model and apikey:
            return None

        configs = [("api", bool(api)), ("model", bool(model)), ("apikey", bool(apikey))]
        missing = ", ".join(x for x, ok in configs if not ok)

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
                    "name"        : tool.name,
                    "description" : tool.description,
                    "parameters"  : Tooling.normalize_openai_schema(tool.inputSchema)
                }
            })

        logger.debug(f"[Tooling] count={len(openai_tools)}")

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
        model_api: dict[str, typing.Any],
        function: typing.Callable[
            [
                ClientSession,
                list[dict[str, typing.Any]],
                dict[str, dict[str, typing.Any]]
            ],
            typing.Awaitable[None]
        ]
    ) -> None:
        """With MCP Session"""

        async def keepalive_loop(req_client: httpx.AsyncClient, stop_event: asyncio.Event) -> None:
            """Keepalive Loop"""
            keepalive_sec = float(const.KEEPALIVE_SEC)

            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=keepalive_sec)
                    break
                except asyncio.TimeoutError:
                    pass

                try:
                    resp = await req_client.get(
                        f"{const.BASE_URL}/api/keepalive",
                        headers={"accept": "application/json"},
                        timeout=float(const.KEEPALIVE_TIMEOUT_SEC)
                    )
                    resp.raise_for_status()

                    payload = resp.json() if resp.headers.get(
                        "content-type", ""
                    ).lower().startswith("application/json") else {}

                    if isinstance(payload, dict):
                        value = payload.get("keepalive_sec")
                        if isinstance(value, (int, float)) and value > 0:
                            keepalive_sec = float(value)
                except Exception as e:
                    logger.debug(f"[Keepalive] failed: {type(e).__name__}: {e}")

        async def inject_auth(req: httpx.Request) -> None:
            """Inject Auth"""
            now = int(time.time())
            if not token_cache["val"] or now - token_cache["ts"] >= 60:
                token_cache["val"] = authentic.manufacture_token()
                token_cache["ts"] = now
            req.headers["Authorization"] = f"Bearer {token_cache['val']}"

        self.ensure_model_api(model_api)

        url = const.BASE_URL + const.MCP_ED
        token_cache = {"ts": 0, "val": ""}
        timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)

        event_hooks = {
            "request": [inject_auth], "response": [request.cap_response]
        }

        async with httpx.AsyncClient(
            timeout=timeout,
            event_hooks=event_hooks,
            trust_env=False
        ) as client:
            keepalive_stop = asyncio.Event()
            keepalive_task: typing.Optional[asyncio.Task[None]] = None

            try:
                async with streamable_http_client(url, http_client=client) as (r, w, _):
                    async with ClientSession(r, w) as session:
                        await session.initialize()
                        list_tools = await session.list_tools()
                        openai_tools, domains = self.build_openai_tools(list_tools)

                        keepalive_task = asyncio.create_task(
                            keepalive_loop(client, keepalive_stop)
                        )

                        await function(session, openai_tools, domains)
            finally:
                keepalive_stop.set()

                if keepalive_task:
                    keepalive_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await keepalive_task

    async def with_mcp_guard(
        self,
        runner: typing.Callable[..., typing.Awaitable[None]],
        *,
        anim_mode: typing.Literal["chat", "fast", "plan"] = "chat",
        **kwargs
    ) -> None:
        """统一执行保护：动画 + 网络/HTTP/通用异常捕获"""

        def flatten_exceptions(exc: BaseException) -> typing.Generator[BaseException, None, None]:
            if isinstance(exc, BaseExceptionGroup):
                for sub in exc.exceptions:
                    yield from flatten_exceptions(sub)
            else:
                yield exc

        await self.start_anim(anim_mode)

        try:
            await runner(**kwargs)

        except* (httpx.ConnectError, httpx.ProxyError, httpx.TimeoutException) as eg:
            for ex in flatten_exceptions(eg):
                logger.error(f"❌ [NET] {ex!r}")

        except* httpx.HTTPStatusError as eg:
            for ex in flatten_exceptions(eg):
                if isinstance(ex, httpx.HTTPStatusError):
                    body = ex.response.extensions.get("error_body", b"")
                    text = body.decode(const.CHARSET, errors="replace")
                    logger.error(f"❌ [HTTP] {ex.response.status_code} {text}")
                else:
                    logger.error(f"❌ [HTTP] unexpected: {ex!r}")

        except* Exception as eg:
            for ex in flatten_exceptions(eg):
                logger.error(f"❌ [ERROR] {ex!r}")

        finally:
            await self.stop_anim()

    async def wakeup(
        self,
        session: ClientSession,
        slog: typing.Optional[StreamTyperLogger] = None
    ) -> typing.Optional[str]:
        """Wakeup"""

        if ((now := time.time()) - self.last_refresh_ts) < self.ttl_sec:
            tip = f"ttl-hit: skip refresh ttl={self.ttl_sec:.3f}s"
            if slog and self.level != const.SHOW_LEVEL:
                return await slog.feed(tip, display=StreamTyperLogger.BLOCK)
            else:
                return logger.debug(tip)

        result = await session.call_tool("refresh", {"ttl_sec": self.ttl_sec})
        ok = (not result.isError)
        content = result.content[0].text

        if not ok:
            return content

        self.last_refresh_ts = now

        if slog and self.level != const.SHOW_LEVEL:
            return await slog.feed(content, display=StreamTyperLogger.BLOCK)
        else:
            return logger.debug(content)

    async def calling(
        self,
        model_api: typing.Optional[dict[str, typing.Any]] = None,
        *,
        message: str,
        func: typing.Callable,
        mode: typing.Literal["chat", "fast", "plan"] = "chat",
        **kwargs
    ) -> None:
        """Calling"""

        model_api = model_api or self.pref.to_config()

        meta_in = kwargs.get("metadata") or {}
        cid = meta_in.get("cid") if isinstance(meta_in, dict) else None
        sid = meta_in.get("sid") if isinstance(meta_in, dict) else None
        kwargs["metadata"] = self.begin_session(cid=cid, sid=sid)

        return await self.with_mcp_guard(
            func, mode=mode, model_api=model_api, message=message, **kwargs
        )

    # workflow: ==== 对话事件模式 ====
    async def stream_looper(
        self,
        session: ClientSession,
        mode: typing.Literal["chat", "fast"],
        model_api: dict[str, typing.Any],
        message: str,
        openai_tools: list[dict[str, typing.Any]],
        domains: dict[str, dict[str, typing.Any]],
        *_,
        **kwargs
    ) -> None:
        """Consume stable turn events for chat/fast modes."""

        exclude = [
            {"domain": "common", "class": "inspect", "name": "free_rule"}
        ]
        if mode == "fast":
            exclude = [
                {"domain": "device"},
                {"domain": "bench", "class": "framix"},
                {"domain": "bench", "class": "memrix"},
                {"domain": "common", "class": "inspect"},
                {"domain": "media", "class": "screen"}
            ]

        ft = Tooling.filter_tools(openai_tools=openai_tools, tool_meta=domains, exclude=exclude)

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

        slog: StreamTyperLogger = StreamTyperLogger(self.report.log_papers)
        await slog.open()
        anim_stopped = False

        # workflow: ==== Stable Turn Event Streaming ====
        try:
            async for event in request.stream_chat(mode, model_api, message, ft, **kwargs):
                if not anim_stopped:
                    await self.stop_anim()
                    anim_stopped = True
                await slog.start()

                match event.get("type"):
                    case "turn.failed":
                        error = str(event.get("error") or "unknown error")
                        await slog.feed(error, display=StreamTyperLogger.BLOCK)
                        await finish("fail", error=error)
                        return await slog.stop()

                    case "text.delta":
                        await slog.feed(str(event.get("text") or ""), display=StreamTyperLogger.STREAM)
                        continue

                    case "text.done":
                        continue

                    case "turn.done":
                        continue

                    case "tool.call":
                        name, arguments = event["name"], event.get("arguments", {})

                        if Tooling.needs_wakeup(domains, name):
                            if error := await self.wakeup(session, slog):
                                await slog.feed(error, display=StreamTyperLogger.BLOCK)
                                await finish("fail", error=str(error))
                                return await slog.stop()

                        await slog.feed(
                            f"{name} {arguments}",
                            display=StreamTyperLogger.BLOCK,
                            display_chunk=Tooling.summarize_tool_arguments(name, arguments)
                        )

                        # workflow: ==== 参数增强 ====
                        arguments = Enhancer.exchange(name, arguments, self.report)

                        # workflow: ==== 工具调用 ====
                        result = await session.call_tool(name, arguments)
                        ok = (not result.isError)

                        # workflow: ==== 工具增强 ====
                        enhancer: Enhancer = Enhancer(session, mode, model_api, kwargs.get("metadata"))
                        fields = await enhancer.enhance(name, arguments, result, ok, slog)

                        await slog.feed(
                            f"{fields.get('text')}", display=StreamTyperLogger.BLOCK
                        )

                        await request.post_tool_result(
                            event["cid"], event["sid"], event["call_id"], name, ok, fields
                        )

                        continue

                    case "tool.output":
                        continue

                    case _:
                        continue

        except Exception as e:
            await slog.feed(str(e), display=StreamTyperLogger.BLOCK)
            await finish("fail", error=f"{type(e).__name__}: {e}")
            return await slog.stop()

        await finish("done")
        await slog.stop()

    # workflow: ==== Plan 编排模式 ====
    async def static_looper(
        self,
        session: ClientSession,
        mode: typing.Literal["plan"],
        model_api: dict[str, typing.Any],
        message: str,
        openai_tools: list[dict[str, typing.Any]],
        domains: dict[str, dict[str, typing.Any]],
        *_,
        **kwargs
    ) -> None:
        """Plan Exec Looper"""

        exclude = [
            {"domain": "common", "class": "security"},
            {"domain": "common", "class": "runtime", "name": "loop_steps"},
            {"domain": "bench", "class": "nexus"}
        ]
        ft = Tooling.filter_tools(openai_tools, domains, exclude=exclude)

        ev_report: typing.Optional[EventReport] = kwargs.pop("ev_report", None)

        def emit(ev: dict[str, typing.Any]) -> None:
            if ev_report:
                ev_report.emit(ev)

        async def finish(phase: str, **extra) -> None:
            if not ev_report: return None
            ev_report.emit({
                "type"  : "lifecycle",
                "scope" : "plan",
                "phase" : phase,
                "ts"    : time.time(),
                **extra
            })
            await ev_report.flush()

        slog: StreamTyperLogger = StreamTyperLogger(self.report.log_papers)

        probes = await session.call_tool("refresh", {"ttl_sec": self.ttl_sec})
        extras = None if probes.isError else {"devices": probes.content[0].text}

        emit({
            "type"  : "lifecycle",
            "scope" : "plan",
            "phase" : "start",
            "mode"  : mode,
            "ts"    : time.time()
        })

        runtime_context: dict[str, typing.Any] = {
            "goal"       : message,
            "mode"       : mode,
            "reasoning"  : "",
            "loop_count" : 1,
            "metadata"   : kwargs.get("metadata") or {},
            "steps"      : [],
            "current"    : None
        }
        anim_stopped = False

        async for plan in request.stream_plan(mode, model_api, message, ft, extras, **kwargs):
            if not anim_stopped:
                await self.stop_anim()
                anim_stopped = True
            if plan.get("type") == "error":
                await finish("fail", error=json.dumps(plan, ensure_ascii=False))
                return logger.error(f"{plan}\n")

            steps, loop_count, reasoning = plan["steps"], plan["loop_count"], plan["reasoning"]
            runtime_context["reasoning"] = reasoning
            runtime_context["loop_count"] = loop_count

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

                    step_context: dict[str, typing.Any] = {
                        "run"     : index,
                        "index"   : step_idx,
                        "total"   : len(steps),
                        "name"    : name,
                        "args"    : arguments,
                        "ok"      : None,
                        "text"    : "",
                        "data"    : None,
                        "cost_ms" : 0
                    }
                    runtime_context["current"] = step_context

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

                    if Tooling.needs_wakeup(domains, name):
                        if error := await self.wakeup(session):
                            await finish("fail", error=str(error), run=index, index=step_idx, name=name)
                            return logger.error(f"{error}\n")

                    logger.info(Tooling.summarize_tool_arguments(name, arguments))

                    # workflow: ==== 参数增强 ====
                    arguments = Enhancer.exchange(name, arguments, self.report)
                    if name == "free_rule":
                        Design.console.print()
                        arguments = {
                            **arguments,
                            "context": {
                                **(arguments.get("context") or {}),
                                "plan": {
                                    "goal"       : runtime_context["goal"],
                                    "mode"       : runtime_context["mode"],
                                    "reasoning"  : runtime_context["reasoning"],
                                    "loop_count" : runtime_context["loop_count"],
                                    "metadata"   : runtime_context["metadata"],
                                    "steps"      : runtime_context["steps"],
                                    "current"    : runtime_context["current"]
                                }
                            }
                        }

                    call_id = craft.short_uid()

                    emit({
                        "type"      : "tool_call",
                        "call_id"   : call_id,
                        "name"      : name,
                        "arguments" : arguments,
                        "ts"        : time.time()
                    })

                    t0 = time.time()

                    # workflow: ==== 工具调用 ====
                    result = await session.call_tool(name, arguments)
                    ok = (not result.isError)

                    # workflow: ==== 工具增强 ====
                    enhancer: Enhancer = Enhancer(session, mode, model_api, kwargs.get("metadata"))
                    fields = await enhancer.enhance(name, arguments, result, ok, slog)

                    step_context["ok"]      = ok
                    step_context["text"]    = (fields.get("text") if isinstance(fields, dict) else "")
                    step_context["data"]    = (fields.get("data") if isinstance(fields, dict) else None)
                    step_context["cost_ms"] = int((time.time() - t0) * 1000)

                    runtime_context["steps"].append(step_context)
                    runtime_context["current"] = step_context

                    emit({
                        "type"    : "tool_result",
                        "call_id" : call_id,
                        "name"    : name,
                        "ok"      : ok,
                        "text"    : (fields.get("text") if isinstance(fields, dict) else ""),
                        "data"    : (fields.get("data") if isinstance(fields, dict) else None),
                        "cost_ms" : int((time.time() - t0) * 1000),
                        "ts"      : time.time()
                    })

                    data = fields.get("data") if isinstance(fields, dict) else None
                    data_ok = bool(data.get("ok")) if isinstance(data, dict) else False
                    if not ok or not data_ok:
                        step_context["data_ok"] = data_ok
                        brief_err = (fields.get("text") if isinstance(fields, dict) else "step failed")
                        await finish("fail", run=index, index=step_idx, name=name, error=brief_err)
                        return logger.error(f"{fields}\n")

                    logger.info(fields.get("text") if isinstance(fields, dict) else "")

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

            await finish("done")
            await slog.stop()

    # Notes: ==== Chat 对话模式 ====
    async def mind_chat(self, model_api: dict[str, typing.Any], message: str, *_, **kwargs) -> None:
        """Mind Chat"""
        async def function(
            session: ClientSession,
            openai_tools: list[dict[str, typing.Any]],
            domains: dict[str, dict[str, typing.Any]]
        ) -> None:
            """Function"""
            mode: typing.Literal["chat"] = "chat"
            await self.stream_looper(
                session, mode, model_api, message, openai_tools, domains, **kwargs
            )

        return await self.with_mcp_session(model_api, function)

    # Notes: ==== Fast 性能模式 ====
    async def mind_fast(self, model_api: dict[str, typing.Any], message: str, *_, **kwargs) -> None:
        """Mind Fast"""
        async def function(
            session: ClientSession,
            openai_tools: list[dict[str, typing.Any]],
            domains: dict[str, dict[str, typing.Any]]
        ) -> None:
            """Function"""
            mode: typing.Literal["fast"] = "fast"
            await self.stream_looper(
                session, mode, model_api, message, openai_tools, domains, **kwargs
            )

        return await self.with_mcp_session(model_api, function)

    # Notes: ==== Plan 编排模式 ====
    async def mind_plan(self, model_api: dict[str, typing.Any], message: str, *_, **kwargs) -> None:
        """Mind Plan"""
        async def function(
            session: ClientSession,
            openai_tools: list[dict[str, typing.Any]],
            domains: dict[str, dict[str, typing.Any]]
        ) -> None:
            """Function"""
            mode: typing.Literal["plan"] = "plan"
            await self.static_looper(
                session, mode, model_api, message, openai_tools, domains, **kwargs
            )

        return await self.with_mcp_session(model_api, function)

    # Notes: ==== Loop 循环模式 ====
    async def mind_loop(self) -> None:
        """Mind Loop"""
        async def exchange(
            matcher: re.Match[str],
            types: typing.Literal["model", "apikey"]
        ) -> typing.Optional[str]:
            """Exchange"""
            if pref_name := matcher.group(1).strip() if matcher.group(1) else None:
                return pref_name

            styles: list[str] = []

            match types:
                case "model":
                    styles = ["<model> (Model name or ID)"]
                case "apikey":
                    styles = ["<apikey> (Provider API key)"]

            for s in styles: Design.console.print(f"[bold #AFC7D8]  • {s}[/]")
            return Design.console.print(f"[bold #FF5F5F]\n {types} invalid: /{types} {const.ERR}{pref_name}")

        async def function(
            session: ClientSession,
            openai_tools: list[dict[str, typing.Any]],
            domains: dict[str, dict[str, typing.Any]]
        ) -> None:
            """Loop with shared MCP session"""

            pref_cfg = self.pref.to_config()
            primary  = pref_cfg.get("primary") or {}
            model    = primary.get("model", "")
            apikey   = primary.get("apikey", "")

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
            [bold #FFD75F]/chat[/]                     对话模式（全域能力接入/自然语言交互）
            [bold #FFD75F]/fast[/]                     高速模式（高吞吐任务流/数据媒体直达）
            [bold #FFD75F]/plan[/]                     编排模式（结构任务拆解/确定路径执行）
            [/]"""

            re_model  = re.compile(r"^\s*/model(?:\s+(.*))?\s*$", re.IGNORECASE)
            re_apikey = re.compile(r"^\s*/apikey(?:\s+(.*))?\s*$", re.IGNORECASE)

            tag: typing.Literal["CHAT", "FAST", "PLAN"] = "CHAT"

            while not self.task_event.is_set():
                try:
                    raw = await self.prompt_box.prompt_async(tag=tag, model=model)
                except KeyboardInterrupt:
                    self.task_event.set()
                    break
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
                    Design.console.print()
                    tag = "CHAT"
                    continue

                if raw.lower() == "/fast":
                    Design.console.print()
                    tag = "FAST"
                    continue

                if raw.lower() == "/plan":
                    Design.console.print()
                    tag = "PLAN"
                    continue

                if m := re_model.match(raw):
                    model = await exchange(m, "model") or model
                    continue

                if m := re_apikey.match(raw):
                    apikey = await exchange(m, "apikey") or apikey
                    continue

                if tag == "CHAT":
                    await self.with_mcp_guard(
                        self.stream_looper,
                        mode="chat",
                        anim_mode="chat",
                        session=session,
                        model_api=model_api,
                        message=raw,
                        openai_tools=openai_tools,
                        domains=domains,
                        metadata=metadata
                    )
                elif tag == "FAST":
                    await self.with_mcp_guard(
                        self.stream_looper,
                        mode="fast",
                        anim_mode="fast",
                        session=session,
                        model_api=model_api,
                        message=raw,
                        openai_tools=openai_tools,
                        domains=domains,
                        metadata=metadata
                    )
                else:
                    await self.with_mcp_guard(
                        self.static_looper,
                        mode="plan",
                        anim_mode="plan",
                        session=session,
                        model_api=model_api,
                        message=raw,
                        openai_tools=openai_tools,
                        domains=domains,
                        metadata=metadata
                    )

        model_api = self.pref.to_config()
        return await self.with_mcp_session(model_api, function)

    # Notes: ==== Pack 批量模式 ====
    async def mind_pack(
        self,
        code: list[str],
        mode: typing.Literal["chat", "fast", "plan"],
        func: typing.Callable[..., typing.Awaitable[None]],
        *_,
        **kwargs
    ) -> None:
        """Mind Pack"""
        code_path = [Path(x).expanduser() for x in (code or [])]
        if not code_path:
            raise MindError("Code list is empty")

        for code_p in code_path:
            if not code_p.exists():
                raise MindError(f"File not found: {code_p}")

        model_api = self.pref.to_config()

        meta_in = kwargs.get("metadata") or {}
        cid = meta_in.get("cid") if isinstance(meta_in, dict) else None
        sid = meta_in.get("sid") if isinstance(meta_in, dict) else None
        kwargs["metadata"] = meta = self.begin_session(cid=cid, sid=sid)

        atlas = f"{const.ATLAS_URL}?mode={mode}&cid={meta['cid']}&sid={meta['sid']}"
        logger.info(f"🌐 Atlas: {atlas}")

        ev_report: EventReport = EventReport(mode, meta["cid"], meta["sid"])
        kwargs["ev_report"] = ev_report
        await ev_report.open()

        async def virtual(
            p: Path,
            items: list[PackItem],
            session: ClientSession,
            openai_tools: list[dict[str, typing.Any]],
            domains: dict[str, dict[str, typing.Any]],
            name: str,
            msg: str,
            run: typing.Optional[int] = None
        ) -> None:
            """Pack Msg"""
            if not msg.strip(): return None
            logger.info(f"🧩 {name} file={p}")

            ev_report.emit({
                "type"  : "lifecycle",
                "scope" : "virtual",
                "phase" : "start",
                "file"  : str(p),
                "name"  : name,
                "run"   : run,
                "ts"    : time.time()
            })

            await self.start_anim(mode)

            try:
                await func(session, mode, model_api, msg, openai_tools, domains, **kwargs)
            except BaseException as exc:
                error = Pack.brief_err(exc)

                ev_report.emit({
                    "type"  : "lifecycle",
                    "scope" : "virtual",
                    "phase" : "fail",
                    "file"  : str(p),
                    "total" : len(items),
                    "error" : error,
                    "name"  : name,
                    "run"   : run,
                    "ts"    : time.time()
                })
                logger.error(f"❌ virtual failed: {name} file={p} err={error}\n")
            finally:
                await self.stop_anim()

            ev_report.emit({
                "type"  : "lifecycle",
                "scope" : "virtual",
                "phase" : "done",
                "file"  : str(p),
                "name"  : name,
                "run"   : run,
                "ts"    : time.time()
            })

        async def packer(
            p: Path,
            session: ClientSession,
            openai_tools: list[dict[str, typing.Any]],
            domains: dict[str, dict[str, typing.Any]]
        ) -> None:
            """Pack File"""
            try:
                text = p.read_text(encoding=const.CHARSET, errors="replace")
            except Exception as e:
                raise MindError(e)

            items, cfg = Pack.pack_parse(text)
            if not items:
                logger.warning(f"Pack has no cases; will run only loop/round hooks. file={p}")

            try:
                repeat = int(cfg.get("repeat") or 1)
            except (TypeError, ValueError):
                repeat = 1
            if repeat < 1:
                repeat = 1

            pattern = (cfg.get("pattern") or "").strip()
            regx    = re.compile(pattern) if pattern else None

            try:
                attempts = int(cfg.get("attempts") or 3)
            except (TypeError, ValueError):
                attempts = 3
            if attempts < 1:
                attempts = 1

            stop_on_fail = str(cfg.get("stop_on_fail") or "").strip().lower() in {
                "1", "true", "yes", "on"
            }

            loop_prefix = (cfg.get("loop_prefix") or "").strip()
            loop_suffix = (cfg.get("loop_suffix") or "").strip()

            round_prefix = (cfg.get("round_prefix") or "").strip()
            round_suffix = (cfg.get("round_suffix") or "").strip()

            item_prefix = (cfg.get("item_prefix") or "").strip()
            item_suffix = (cfg.get("item_suffix") or "").strip()

            global_prefix = (cfg.get("global_prefix") or "").strip()
            global_suffix = (cfg.get("global_suffix") or "").strip()
            global_rule   = (cfg.get("global_rule") or "").strip()

            ev_report.emit({
                "type"   : "lifecycle",
                "scope"  : "batch",
                "phase"  : "start",
                "file"   : str(p),
                "items"  : len(items),
                "repeat" : repeat,
                "ts"     : time.time()
            })

            await virtual(
                p, items, session, openai_tools, domains, "__loop_prefix__", loop_prefix
            )

            try:
                for r in range(1, repeat + 1):
                    await virtual(
                        p, items, session, openai_tools, domains, "__round_prefix__", round_prefix, r
                    )

                    logger.info(f"🧪 run {r}/{repeat} items={len(items)} file={p}")

                    for idx, it in enumerate(items, start=1):
                        if regx and not regx.search(it.name):
                            ev_report.emit({
                                "type"       : "lifecycle",
                                "scope"      : "task",
                                "phase"      : "skip",
                                "file"       : str(p),
                                "run"        : r,
                                "index"      : idx,
                                "total"      : len(items),
                                "name"       : it.name,
                                "item_total" : it.loop,
                                "reason"     : "filter",
                                "ts"         : time.time()
                            })
                            logger.debug(f"⏭️  skip [{idx}/{len(items)}] {it.name} (filter)")
                            continue

                        ev_report.emit({
                            "type"  : "lifecycle",
                            "scope" : "item_hook",
                            "phase" : "start",
                            "hook"  : "item_prefix",
                            "file"  : str(p),
                            "run"   : r,
                            "index" : idx,
                            "total" : len(items),
                            "name"  : it.name,
                            "ts"    : time.time()
                        })
                        await virtual(
                            p, items, session, openai_tools, domains,"__item_prefix__", item_prefix, r
                        )
                        ev_report.emit({
                            "type"  : "lifecycle",
                            "scope" : "item_hook",
                            "phase" : "done",
                            "hook"  : "item_prefix",
                            "file"  : str(p),
                            "run"   : r,
                            "index" : idx,
                            "total" : len(items),
                            "name"  : it.name,
                            "ts"    : time.time()
                        })


                        try:
                            for item_run in range(1, it.loop + 1):
                                ev_report.emit({
                                    "type"       : "lifecycle",
                                    "scope"      : "task",
                                    "phase"      : "start",
                                    "file"       : str(p),
                                    "run"        : r,
                                    "index"      : idx,
                                    "total"      : len(items),
                                    "name"       : it.name,
                                    "item_run"   : item_run,
                                    "item_total" : it.loop,
                                    "ts"         : time.time()
                                })

                                logger.info(
                                    f"▶️  [{idx}/{len(items)}] {it.name} item_run={item_run}/{it.loop} file={p}"
                                )

                                last_error: typing.Optional[str] = None

                                for attempt in range(1, attempts + 1):
                                    t0 = time.time()

                                    ev_report.emit({
                                        "type"         : "lifecycle",
                                        "scope"        : "task",
                                        "phase"        : "attempt",
                                        "file"         : str(p),
                                        "run"          : r,
                                        "index"        : idx,
                                        "total"        : len(items),
                                        "name"         : it.name,
                                        "item_run"     : item_run,
                                        "item_total"   : it.loop,
                                        "attempt"      : attempt,
                                        "max_attempts" : attempts,
                                        "ts"           : time.time()
                                    })

                                    prefix = (it.meta.get("prefix") or global_prefix or "").strip()
                                    suffix = (it.meta.get("suffix") or global_suffix or "").strip()
                                    rule = (it.meta.get("rule") or global_rule or "").strip()

                                    final_msg = it.message
                                    if prefix:
                                        final_msg = f"{prefix}\n{final_msg}"
                                    if suffix:
                                        final_msg = f"{final_msg}\n{suffix}"
                                    if rule:
                                        final_msg = f"{final_msg}\n\n{rule}"

                                    await self.start_anim(mode)

                                    try:
                                        await func(session, mode, model_api, final_msg, openai_tools, domains, **kwargs)

                                        ev_report.emit({
                                            "type"       : "lifecycle",
                                            "scope"      : "task",
                                            "phase"      : "done",
                                            "file"       : str(p),
                                            "run"        : r,
                                            "index"      : idx,
                                            "total"      : len(items),
                                            "name"       : it.name,
                                            "item_run"   : item_run,
                                            "item_total" : it.loop,
                                            "attempt"    : attempt,
                                            "cost_ms"    : int((time.time() - t0) * 1000),
                                            "ts"         : time.time()
                                        })

                                        break

                                    except BaseException as exc:
                                        error = Pack.brief_err(exc)
                                        last_error = error

                                        ev_report.emit({
                                            "type"         : "lifecycle",
                                            "scope"        : "task",
                                            "phase"        : "fail",
                                            "file"         : str(p),
                                            "run"          : r,
                                            "index"        : idx,
                                            "total"        : len(items),
                                            "name"         : it.name,
                                            "item_run"     : item_run,
                                            "item_total"   : it.loop,
                                            "attempt"      : attempt,
                                            "max_attempts" : attempts,
                                            "error"        : error,
                                            "ts"           : time.time()
                                        })

                                        logger.error(
                                            f"❌ item failed: {it.name} "
                                            f"item_run={item_run}/{it.loop} "
                                            f"attempt={attempt}/{attempts} err={error}\n"
                                        )

                                        if attempt < attempts:
                                            backoff = 0.5 * (2 ** (attempt - 1))
                                            ev_report.emit({
                                                "type"       : "lifecycle",
                                                "scope"      : "task",
                                                "phase"      : "retry_wait",
                                                "file"       : str(p),
                                                "run"        : r,
                                                "index"      : idx,
                                                "total"      : len(items),
                                                "name"       : it.name,
                                                "item_run"   : item_run,
                                                "item_total" : it.loop,
                                                "attempt"    : attempt,
                                                "wait_s"     : backoff,
                                                "ts"         : time.time()
                                            })
                                            await asyncio.sleep(backoff)
                                    finally:
                                        await self.stop_anim()

                                else:
                                    ev_report.emit({
                                        "type"         : "lifecycle",
                                        "scope"        : "task",
                                        "phase"        : "give_up",
                                        "file"         : str(p),
                                        "run"          : r,
                                        "index"        : idx,
                                        "total"        : len(items),
                                        "name"         : it.name,
                                        "item_run"     : item_run,
                                        "item_total"   : it.loop,
                                        "max_attempts" : attempts,
                                        "error"        : last_error,
                                        "ts"           : time.time()
                                    })

                                    logger.error(
                                        f"🧯 give up: {it.name} "
                                        f"item_run={item_run}/{it.loop} "
                                        f"attempts={attempts} last={last_error}"
                                    )
                                    if stop_on_fail: return None
                                    continue

                        finally:
                            ev_report.emit({
                                "type"  : "lifecycle",
                                "scope" : "item_hook",
                                "phase" : "start",
                                "hook"  : "item_suffix",
                                "file"  : str(p),
                                "run"   : r,
                                "index" : idx,
                                "total" : len(items),
                                "name"  : it.name,
                                "ts"    : time.time()
                            })
                            await virtual(
                                p, items, session, openai_tools, domains,"__item_suffix__", item_suffix, r
                            )
                            ev_report.emit({
                                "type"  : "lifecycle",
                                "scope" : "item_hook",
                                "phase" : "done",
                                "hook"  : "item_suffix",
                                "file"  : str(p),
                                "run"   : r,
                                "index" : idx,
                                "total" : len(items),
                                "name"  : it.name,
                                "ts"    : time.time()
                            })

                    await virtual(
                        p, items, session, openai_tools, domains, "__round_suffix__", round_suffix, r
                    )

                await virtual(
                    p, items, session, openai_tools, domains, "__loop_suffix__", loop_suffix
                )

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

        async def function(
            session: ClientSession,
            openai_tools: list[dict[str, typing.Any]],
            domains: dict[str, dict[str, typing.Any]]
        ) -> None:
            """Function"""
            try:
                for path in code_path:
                    await packer(path, session, openai_tools, domains)
            finally:
                await ev_report.flush()
                await ev_report.close()

        return await self.with_mcp_session(model_api, function)


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

    # Notes: ========== 工具路径设置 ==========
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
        return await up.upgrade_app(supports)

    for tls in (tools := [helix]):
        os.environ["PATH"] = os.path.dirname(tls) + env_symbol + os.environ.get("PATH", "")

    # 检查每个工具是否存在，如果缺失则显示错误信息并退出程序
    for tls in tools:
        if not shutil.which((tls_name := os.path.basename(tls))):
            raise MindError(f"{const.APP_DESC} missing files {tls_name}")

    # 三方应用以及文件授权
    await authorized()

    # Notes: ========== 启动命令 ==========
    launch_cmd = [helix, "--level", level]

    if cmd_lines.pref:
        server: ServerManage = ServerManage(launch_cmd)
        await server.ensure_running()
        await server.close()
        return await FileAssist.open_url(f"{const.BASE_URL}/pref")

    # Notes: ========== 授权流程 ==========
    lic_file = Path(src_opera_place) / const.LIC_FILE

    if apply_code := cmd_lines.apply:
        return await authorize.receive_license(apply_code, lic_file)

    await authorize.verify_license(lic_file)

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

    server: ServerManage = ServerManage(launch_cmd)
    await server.ensure_running()
    await server.close()
    await pref.load_pref()

    Design.Doc.log(f"[bold #0EA5E9]🌐 Link: {const.BASE_URL}[/]\n")

    positions = (
        cmd_lines.chat, cmd_lines.fast, cmd_lines.plan,
        cmd_lines.gravity, cmd_lines.reflection, cmd_lines.code
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
        await mind.calling(message=chat, func=mind.mind_chat, mode="chat")
    elif fast := cmd_lines.fast:
        await mind.calling(message=fast, func=mind.mind_fast, mode="fast")
    elif plan := cmd_lines.plan:
        await mind.calling(message=plan, func=mind.mind_plan, mode="plan")
    elif code := cmd_lines.code:
        if cmd_lines.chat is not None:
            func = mind.stream_looper
            mode: typing.Literal["chat"] = "chat"
        elif cmd_lines.fast is not None:
            func = mind.stream_looper
            mode: typing.Literal["fast"] = "fast"
        else:
            func = mind.static_looper
            mode: typing.Literal["plan"] = "plan"

        await mind.mind_pack(code, mode, func)

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
