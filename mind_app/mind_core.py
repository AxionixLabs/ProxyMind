# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import typing
import asyncio
from loguru import logger
from mcp import (
    ClientSession, ListToolsResult
)
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

        self.task_event: asyncio.Event = asyncio.Event()

        self.anim_manager: AsyncAnimManager = AsyncAnimManager()

        self.last_refresh_ts: float = 0.0
        self.ttl_sec: float = 1.0

        self.design: Design = Design(self.level)

        self.cid: typing.Optional[str] = None
        self.sid: typing.Optional[str] = None

        self.report: Report = Report(self.src_total_place, self.gravity)
        self.prompt_box: PromptToolkitBox = PromptToolkitBox()
        self.attach: Attach = Attach()

        self.runtime_loop: typing.Optional[asyncio.AbstractEventLoop] = None
        self.root_task: typing.Optional[asyncio.Task[typing.Any]] = None

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
        root_task: typing.Optional[asyncio.Task[typing.Any]],
    ) -> None:
        """绑定当前事件循环与顶层任务，用于异步退出。"""
        self.runtime_loop = loop
        self.root_task = root_task

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
            lambda stop_event: self.design.stream_wait_live(stop_event, mode)
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
    def ensure_model_api(model_api: dict[str, typing.Any]) -> None:
        """校验模型配置中的关键字段是否完整。"""
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
        model_api: dict[str, typing.Any],
        function: typing.Callable[
            [
                ClientSession,
                list[dict[str, typing.Any]],
                dict[str, dict[str, typing.Any]],
            ],
            typing.Awaitable[None],
        ],
    ) -> None:
        """MCP 会话入口：把共享连接与工具集构建委托给运行时模块。"""
        return await run_with_mcp_session(self, model_api, function)

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
        session: ClientSession,
        slog: typing.Optional[StreamUI] = None
    ) -> typing.Optional[str]:
        """刷新入口：按 TTL 规则委托运行时模块执行 refresh。"""
        return await run_wakeup(self, session, slog)

    async def calling(
        self,
        model_api: typing.Optional[dict[str, typing.Any]] = None,
        *,
        message: str,
        mode: RunMode = "chat",
        **kwargs,
    ) -> None:
        """调用入口：统一委托运行时模块按 mode 执行单次请求。"""
        return await run_calling(
            self,
            model_api=model_api,
            message=message,
            mode=mode,
            **kwargs
        )

    async def stream_looper(
        self,
        session: ClientSession,
        mode: typing.Literal["chat", "fast", "xtra"],
        model_api: dict[str, typing.Any],
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
            model_api,
            message,
            openai_tools,
            tool_meta,
            **kwargs
        )

    async def static_looper(
        self,
        session: ClientSession,
        mode: typing.Literal["plan"],
        model_api: dict[str, typing.Any],
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
            model_api,
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
