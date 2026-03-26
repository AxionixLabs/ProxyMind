# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import typing
import asyncio
from loguru import logger
from mcp import (
    ClientSession, ListToolsResult
)
from engine.animaion import AsyncAnimManager
from engine.tinker import (
    MindError, Tooling, StreamTyperLogger
)
from mindcore.design import Design
from mindcore.prompting import PromptToolkitBox
from mindcore.preference import Preferences
from mindnova.report import Report
from mindnova import craft
from .mind_modes import (
    mind_chat as run_mind_chat,
    mind_fast as run_mind_fast,
    mind_loop as run_mind_loop,
    mind_plan as run_mind_plan,
    static_looper as run_static_looper,
    stream_looper as run_stream_looper,
)
from .mind_batch import mind_pack as run_mind_pack
from .mind_runtime import (
    calling as run_calling,
    wakeup as run_wakeup,
    with_mcp_guard as run_with_mcp_guard,
    with_mcp_session as run_with_mcp_session
)


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
        self.task_info: list = []

        self.anim_manager: AsyncAnimManager = AsyncAnimManager()

        self.last_refresh_ts: float = 0.0
        self.ttl_sec: float = 1.0

        self.design: Design = Design(self.level)

        self.cid: typing.Optional[str] = None
        self.sid: typing.Optional[str] = None

        self.report: Report = Report(self.src_total_place, self.gravity)
        self.prompt_box: PromptToolkitBox = PromptToolkitBox()

    @property
    def remote(self) -> dict:
        """返回远程全局配置。"""
        return self.__remote

    @remote.setter
    def remote(self, value: dict) -> None:
        """设置远程全局配置，并在异常输入时兜底为空字典。"""
        self.__remote = value if isinstance(value, dict) else {}

    def signal_processor(self, *_, **__) -> None:
        """处理终止信号，并执行统一退出流程。"""
        self.task_event.set()
        Design.console.print()
        Design.show_exit()
        sys.exit(130)

    async def stop_anim(self) -> None:
        """停止等待动画。"""
        await self.anim_manager.stop()

    async def start_anim(self, mode: typing.Literal["chat", "fast", "plan"] = "chat") -> None:
        """启动指定模式的等待动画。"""
        await self.anim_manager.start(
            lambda stop_event: self.design.stream_wait_live(stop_event, mode)
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
            if bool((meta := tool.meta).get("hidden", False)):
                continue
            tool_meta[tool.name] = meta

            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name"        : tool.name,
                        "description" : tool.description,
                        "parameters"  : Tooling.normalize_openai_schema(tool.inputSchema)
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
        anim_mode: typing.Literal["chat", "fast", "plan"] = "chat",
        **kwargs
    ) -> None:
        """执行保护入口：统一委托给运行时模块处理动画和异常。"""

        return await run_with_mcp_guard(self, runner, anim_mode=anim_mode, **kwargs)

    async def wakeup(
        self,
        session: ClientSession,
        slog: typing.Optional[StreamTyperLogger] = None
    ) -> typing.Optional[str]:
        """刷新入口：按 TTL 规则委托运行时模块执行 refresh。"""
        return await run_wakeup(self, session, slog)

    async def calling(
        self,
        model_api: typing.Optional[dict[str, typing.Any]] = None,
        *,
        message: str,
        func: typing.Callable,
        mode: typing.Literal["chat", "fast", "plan"] = "chat",
        **kwargs,
    ) -> None:
        """调用入口：统一委托运行时模块处理 metadata 和事件报告。"""
        return await run_calling(
            self,
            model_api=model_api,
            message=message,
            runner=func,
            mode=mode,
            **kwargs
        )

    async def stream_looper(
        self,
        session: ClientSession,
        mode: typing.Literal["chat", "fast"],
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

    async def mind_chat(self, model_api: dict[str, typing.Any], message: str, *_, **kwargs) -> None:
        """对话模式入口：委托给模式调度模块。"""
        return await run_mind_chat(self, model_api, message, **kwargs)

    async def mind_fast(self, model_api: dict[str, typing.Any], message: str, *_, **kwargs) -> None:
        """高速模式入口：委托给模式调度模块。"""
        return await run_mind_fast(self, model_api, message, **kwargs)

    async def mind_plan(self, model_api: dict[str, typing.Any], message: str, *_, **kwargs) -> None:
        """编排模式入口：委托给模式调度模块。"""
        return await run_mind_plan(self, model_api, message, **kwargs)

    async def mind_loop(self) -> None:
        """交互循环入口：委托给模式调度模块。"""
        return await run_mind_loop(self)

    async def mind_pack(
        self,
        code: list[str],
        mode: typing.Literal["chat", "fast", "plan"],
        func: typing.Callable[..., typing.Awaitable[None]],
        *_,
        **kwargs
    ) -> None:
        """批处理入口：委托给批处理模块。"""
        return await run_mind_pack(self, code, mode, func, **kwargs)


if __name__ == '__main__':
    pass
