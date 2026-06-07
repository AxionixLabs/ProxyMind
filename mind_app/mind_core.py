# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import time
import typing
import asyncio
import contextlib
from loguru import logger
from mcp import ListToolsResult
from engine.manage import ServerManage
from engine.animation import AsyncAnimManager
from engine.tinker import MindError
from mind_core.design import Design
from mind_core.prompting import PromptToolkitBox
from mind_core.preference import Preferences
from mind_nova.modes import RunMode
from mind_nova.report import Report
from mind_nova import craft
from .attach import Attach
from .stream_ui import StreamUI
from .modes.repl import mind_loop as run_mind_loop
from .modes.static import static_looper as run_static_looper
from .modes.stream import stream_looper as run_stream_looper
from .modes.batch import mind_pack as run_mind_pack
from .modes.agent import run_agent_loop
from .runtime.calling import (
    calling as run_calling,
    wakeup as run_wakeup,
    with_mcp_guard as run_with_mcp_guard
)
from .runtime.session import with_mcp_session as run_with_mcp_session
from .runtime.keepalive import run_keepalive
from .runtime.external_mcp import ExternalMcpRuntime
from .mcp import McpSessionLike


class Mind(object):
    """Mind 核心对象：维护共享状态，并暴露稳定的应用接口。"""

    __remote: dict = {}

    def __init__(self, wires: list, level: str, power: int, remote: dict, *args, **kwargs):
        self.wires = wires
        self.level = level
        self.power = power

        self.remote: dict = remote or {}

        *_, self.gravity, self.reflection, _ = args

        self.src_opera_place: str = kwargs["src_opera_place"]
        self.src_total_place: str = kwargs["src_total_place"]

        self.pref: Preferences = kwargs["pref"]
        self.pref_refreshed_at: float = time.monotonic()
        self.pref_refresh_ttl_sec: float = 1.0

        self.task_event: asyncio.Event = asyncio.Event()

        self.anim_manager: AsyncAnimManager = AsyncAnimManager()

        self.last_refresh_ts: float = 0.0
        self.ttl_sec: float         = 1.0

        self.design: Design = Design(self.level)

        self.cid: typing.Optional[str] = None
        self.sid: typing.Optional[str] = None

        self.report: Report = Report(self.src_total_place, self.gravity)
        self.prompt_box: PromptToolkitBox = PromptToolkitBox()
        self.attach: Attach = Attach()

        self.runtime_loop: typing.Optional[asyncio.AbstractEventLoop] = None
        self.root_task: typing.Optional[asyncio.Task[typing.Any]]     = None
        self.server_manager: typing.Optional[ServerManage]            = None
        self.keepalive_stop: typing.Optional[asyncio.Event]           = None
        self.keepalive_task: typing.Optional[asyncio.Task[None]]      = None
        self.external_mcp: typing.Optional[ExternalMcpRuntime]        = None

        self.exit_code: int = 0
        self.sig_count: int = 0

    @property
    def remote(self) -> dict:
        """返回远程全局配置。"""
        return self.__remote

    @remote.setter
    def remote(self, value: dict) -> None:
        """设置远程全局配置，并在异常输入时兜底为空字典。"""
        self.__remote = value if isinstance(value, dict) else {}

    def bind_runtime(
        self,
        loop: asyncio.AbstractEventLoop,
        root_task: typing.Optional[asyncio.Task[typing.Any]]
    ) -> None:
        """绑定当前事件循环与顶层任务，用于异步退出。"""
        self.runtime_loop = loop
        self.root_task    = root_task

    def bind_server_manager(self, server_manager: ServerManage) -> None:
        """绑定本地后台服务管理器。"""
        self.server_manager = server_manager

    async def refresh_pref_if_stale(self, *, ttl_sec: typing.Optional[float] = None) -> None:
        """按 TTL 从后端刷新偏好配置，用于模型与密钥热更新。"""
        refresh_ttl = self.pref_refresh_ttl_sec if ttl_sec is None else max(0.0, float(ttl_sec))
        now = time.monotonic()
        if self.pref_refreshed_at and (now - self.pref_refreshed_at) < refresh_ttl:
            return None

        try:
            await self.pref.load_pref()
        except Exception as exc:
            logger.debug(f"[Pref] refresh skipped: {type(exc).__name__}: {exc}")
            return None

        self.pref_refreshed_at = time.monotonic()

    async def fresh_pref_config(self, *, ttl_sec: typing.Optional[float] = None) -> dict[str, typing.Any]:
        """返回刷新后的偏好配置快照。"""
        await self.refresh_pref_if_stale(ttl_sec=ttl_sec)
        return self.pref.to_config()

    def start_keepalive_supervisor(self) -> None:
        """启动 Mind 生命周期内的本地后台服务保活任务。"""
        if self.keepalive_task and not self.keepalive_task.done():
            return None

        self.keepalive_stop = asyncio.Event()
        self.keepalive_task = asyncio.create_task(
            run_keepalive(
                self.keepalive_stop,
                server_manager=self.server_manager
            ),
            name="local service keepalive"
        )

    async def start_external_mcp_runtime(self) -> None:
        """启动 Mind 生命周期级外部 MCP 运行时。"""
        if self.external_mcp is None:
            self.external_mcp = ExternalMcpRuntime(self)
        await self.external_mcp.start()

    async def stop_external_mcp_runtime(self) -> None:
        """停止 Mind 生命周期级外部 MCP 运行时。"""
        runtime = self.external_mcp
        self.external_mcp = None
        if runtime is not None:
            await runtime.stop()

    async def stop_keepalive_supervisor(self) -> None:
        """停止 Mind 生命周期内的本地后台服务保活任务。"""
        if self.keepalive_stop is not None:
            self.keepalive_stop.set()

        task = self.keepalive_task
        self.keepalive_task = None
        self.keepalive_stop = None

        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def close_runtime_resources(self) -> None:
        """关闭 Mind 持有的运行时资源，不关闭本地后台进程。"""
        await self.stop_external_mcp_runtime()
        await self.stop_keepalive_supervisor()
        if self.server_manager is not None:
            await self.server_manager.close()
            self.server_manager = None

    async def reboot_runtime(self) -> None:
        """重启已绑定的后台进程，并在完成后恢复保活任务。"""
        if self.server_manager is None:
            raise MindError("Server manager is not bound")

        await self.stop_keepalive_supervisor()
        try:
            await self.server_manager.restart()
            if not await self.server_manager.wait_until_ready(10.0, 0.3):
                raise MindError("Server not ready after reboot")
        finally:
            self.start_keepalive_supervisor()

    def _cancel_root_task(self) -> None:
        """取消顶层任务，让退出沿协程栈执行清理逻辑。"""
        task = self.root_task
        if task is not None and not task.done():
            task.cancel()

    def signal_processor(self, *_, **__) -> None:
        """处理终止信号，并优先触发异步清理。"""
        self.sig_count += 1
        self.task_event.set()
        self.exit_code = 130

        if self.sig_count > 1:
            sys.exit(self.exit_code)

        loop = self.runtime_loop
        if loop is not None and loop.is_running():
            self._cancel_root_task()
            return None

        sys.exit(self.exit_code)

    @staticmethod
    async def await_cleanup(awaitable: typing.Awaitable[None]) -> None:
        """在取消态下也等待清理逻辑执行完成。"""
        task = asyncio.ensure_future(awaitable)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def stop_anim(self) -> None:
        """停止等待动画。"""
        await self.anim_manager.stop()

    async def start_anim(self, mode: RunMode = "chat") -> None:
        """启动指定模式的等待动画。"""
        await self.anim_manager.start(
            lambda stop_event: self.design.stream_mode_live(stop_event, mode)
        )

    async def start_upload_anim(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]]
    ) -> None:
        """启动附件上传动画，并复用统一动画管理器避免冲突。"""
        await self.anim_manager.start(
            lambda stop_event: self.design.upload_progress_live(stop_event, snapshot)
        )

    async def start_external_mcp_anim(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]]
    ) -> None:
        """启动外部 MCP 启动状态动画。"""
        await self.anim_manager.start(
            lambda stop_event: self.design.external_mcp_live(stop_event, snapshot)
        )

    @staticmethod
    def ensure_pref_config(pref_config: dict[str, typing.Any]) -> None:
        """校验偏好配置中的关键字段是否完整。"""
        primary = pref_config.get("primary") if isinstance(pref_config, dict) else None
        if isinstance(primary, dict):
            api    = primary.get("api")
            model  = primary.get("model")
            apikey = primary.get("apikey")
        else:
            api    = pref_config["api"]
            model  = pref_config["model"]
            apikey = pref_config["apikey"]

        if api and model and apikey:
            return None

        configs = [("api", bool(api)), ("model", bool(model)), ("apikey", bool(apikey))]
        missing = ", ".join(x for x, ok in configs if not ok)

        raise MindError(f"Missing required field(s): {missing}")

    @staticmethod
    def build_openai_tools(
        list_tools: ListToolsResult,
    ) -> tuple[list[dict[str, typing.Any]], dict[str, dict[str, typing.Any]]]:
        """把 MCP 工具列表转换为 OpenAI 兼容的工具描述。"""
        openai_tools: list[dict[str, typing.Any]] = []
        tool_meta: dict[str, dict[str, typing.Any]] = {}

        for tool in list_tools.tools:
            meta = dict(tool.meta or {})
            if bool(meta.get("hidden", False)):
                continue
            tool_meta[tool.name] = meta

            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name"        : tool.name,
                        "description" : tool.description,
                        "parameters"  : tool.inputSchema
                    }
                }
            )

        logger.debug(f"[Tooling] count={len(openai_tools)}")

        return openai_tools, tool_meta

    def begin_session(
        self,
        cid: typing.Optional[str] = None,
        sid: typing.Optional[str] = None,
    ) -> dict[str, str]:
        """初始化或续用当前会话标识。"""
        self.cid = cid or self.cid or craft.new_cid()
        self.sid = sid or self.sid or craft.new_sid(self.cid)

        return {"cid": self.cid, "sid": self.sid}

    async def with_mcp_session(
        self,
        pref_config: dict[str, typing.Any],
        function: typing.Callable[
            [
                McpSessionLike,
                list[dict[str, typing.Any]],
                dict[str, dict[str, typing.Any]],
            ],
            typing.Awaitable[None],
        ],
        before_user_flow: typing.Optional[typing.Callable[[], typing.Any]] = None
    ) -> None:
        """MCP 会话入口：把共享连接与工具集构建委托给运行时模块。"""
        return await run_with_mcp_session(
            self,
            pref_config,
            function,
            before_user_flow=before_user_flow
        )

    async def with_mcp_guard(
        self,
        runner: typing.Callable[..., typing.Awaitable[None]],
        *,
        mode: RunMode = "chat",
        **kwargs
    ) -> None:
        """执行保护入口：统一委托给运行时模块处理动画和异常。"""
        return await run_with_mcp_guard(self, runner, mode=mode, **kwargs)

    async def wakeup(
        self,
        session: McpSessionLike,
        slog: typing.Optional[StreamUI] = None
    ) -> typing.Optional[str]:
        """刷新入口：按 TTL 规则委托运行时模块执行 refresh。"""
        return await run_wakeup(self, session, slog)

    async def calling(
        self,
        pref_config: typing.Optional[dict[str, typing.Any]] = None,
        *,
        message: str,
        mode: RunMode = "chat",
        **kwargs,
    ) -> None:
        """调用入口：统一委托运行时模块按 mode 执行单次请求。"""
        return await run_calling(
            self,
            pref_config=pref_config,
            message=message,
            mode=mode,
            **kwargs
        )

    async def stream_looper(
        self,
        session: McpSessionLike,
        mode: typing.Literal["chat", "fast", "xtra"],
        pref_config: dict[str, typing.Any],
        message: str,
        openai_tools: list[dict[str, typing.Any]],
        tool_meta: dict[str, dict[str, typing.Any]],
        *_,
        **kwargs
    ) -> None:
        """流式执行入口：委托给流式模式模块。"""
        return await run_stream_looper(
            self,
            session,
            mode,
            pref_config,
            message,
            openai_tools,
            tool_meta,
            **kwargs
        )

    async def static_looper(
        self,
        session: McpSessionLike,
        mode: typing.Literal["plan"],
        pref_config: dict[str, typing.Any],
        message: str,
        openai_tools: list[dict[str, typing.Any]],
        tool_meta: dict[str, dict[str, typing.Any]],
        *_,
        **kwargs
    ) -> None:
        """静态执行入口：委托给编排模式模块。"""
        return await run_static_looper(
            self,
            session,
            mode,
            pref_config,
            message,
            openai_tools,
            tool_meta,
            **kwargs
        )

    async def mind_loop(self) -> None:
        """交互循环入口：委托给模式调度模块。"""
        return await run_mind_loop(self)

    async def mind_pack(
        self,
        code: list[typing.Any],
        mode: RunMode,
        *_,
        **kwargs
    ) -> None:
        """批处理入口：委托给批处理模块。"""
        return await run_mind_pack(self, code, mode, **kwargs)

    async def agent_loop(self) -> None:
        """订阅模式入口：委托给订阅模式模块。"""
        return await run_agent_loop(self)


if __name__ == '__main__':
    pass
