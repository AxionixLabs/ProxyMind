# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import copy
import time
import typing
import asyncio
import sqlite3
import contextlib
from pathlib import Path
from loguru import logger
from engine.manage import ServerManage
from engine.animation import AsyncAnimManager
from engine.tinker import MindError
from mind_nova import craft
from mind_core.design import Design
from mind_core.preference import Preferences
from mind_nova.modes import (
    DEFAULT_RUN_MODE,
    RunMode
)
from mind_nova.report import Report
from .attach import Attach
from .modes.repl import mind_loop as run_mind_loop
from .modes.stream import stream_looper as run_stream_looper
from .modes.batch import mind_pack as run_mind_pack
from .modes.agent import run_agent_loop
from .runtime.support.calling import (
    calling as run_calling,
    run_mode_lifecycle as run_mode_lifecycle_wrapper
)
from .runtime.mcp.keepalive import run_keepalive
from .runtime.mcp.external import ExternalMcpRuntime
from server import ConfigServiceRuntime
from .runtime.support.conversation import ConversationState
from .runtime.mcp.tool_runtime import (
    CompositeToolRuntime,
    ToolRuntime
)
from .client_tools import (
    ClientToolRegistry,
    default_registry as default_client_tool_registry
)
from .native_coding import NativeCoding
from .output.factory import OutputMode, create_output_session
from .output.session import SessionFactory
from .interaction.contracts import InteractionPort
from .interaction.legacy import LegacyInteraction
from .history import (
    ConversationHistoryStore,
    HISTORY_LIMIT,
    normalize_workspace
)
from .history.ids import valid_session_ids
from .mcp import McpSessionLike

if typing.TYPE_CHECKING:
    from .runtime.mcp.service_runtime import ServiceRuntimeContext


class Mind(object):
    """Mind 核心对象：维护共享状态，并暴露稳定的应用接口。"""

    __remote: dict = {}

    def __init__(self, wires: list, level: str, power: int, remote: dict, *args, **kwargs):
        self.wires = wires
        self.level = level
        self.power = power

        self.remote: dict = remote or {}

        *_, self.gravity, _ = args

        self.src_opera_place: str = kwargs["src_opera_place"]
        self.src_total_place: str = kwargs["src_total_place"]

        self.history_workspace: str = normalize_workspace(
            kwargs.get("workspace_root") or Path.cwd()
        )

        self.pref: Preferences = kwargs["pref"]
        self.pref_refreshed_at: float    = time.monotonic()
        self.pref_refresh_ttl_sec: float = 1.0

        self.task_event: asyncio.Event = asyncio.Event()

        self.anim_manager: AsyncAnimManager = kwargs.get("anim_manager") or AsyncAnimManager()

        self.animate: bool = bool(kwargs.get("animate", True))

        output_mode = kwargs.get("output_mode")
        self.output_mode: OutputMode = (
            output_mode if output_mode in {"tui", "text", "json"} else "tui"
        )

        self.design: Design = Design()

        self.conversation: ConversationState         = ConversationState()
        self.history_store: ConversationHistoryStore = ConversationHistoryStore()

        self.report: Report = Report(self.src_total_place, self.gravity)
        self.attach: Attach = Attach()

        interaction = kwargs.get("interaction")

        if interaction is not None:
            self.interaction: InteractionPort = interaction
        else:
            self.interaction = LegacyInteraction()
        self.session_factory: SessionFactory = (
            kwargs.get("session_factory") or create_output_session
        )

        self.native_coding: NativeCoding  = NativeCoding(root=self.history_workspace)

        self.runtime_loop: typing.Optional[asyncio.AbstractEventLoop] = None
        self.root_task: typing.Optional[asyncio.Task[typing.Any]]     = None
        self.server_manager: typing.Optional[ServerManage]            = None
        self.keepalive_stop: typing.Optional[asyncio.Event]           = None
        self.keepalive_task: typing.Optional[asyncio.Task[None]]      = None

        self.service_runtime_context: typing.Optional["ServiceRuntimeContext"] = None
        self.service_exec_env: typing.Optional[dict[str, typing.Any]]          = None

        self.config_service: ConfigServiceRuntime = ConfigServiceRuntime(log_level=self.level)

        self.external_mcp: typing.Optional[ExternalMcpRuntime] = None

        self.client_tools: ClientToolRegistry = self._build_client_tools()
        self.tool_runtime: ToolRuntime        = CompositeToolRuntime(self)

        self.exit_code: int = 0
        self.sig_count: int = 0

        self.service_mcp_linked: bool   = False
        self.stop_runtime_on_exit: bool = False

        self.last_assistant_reply: str = ""

    @property
    def remote(self) -> dict:
        """返回远程全局配置。"""
        return self.__remote

    @remote.setter
    def remote(self, value: dict) -> None:
        """设置远程全局配置，并在异常输入时兜底为空字典。"""
        self.__remote = value if isinstance(value, dict) else {}

    @staticmethod
    def keepalive_task_done(task: asyncio.Task[None]) -> None:
        """回收后台保活任务异常，避免事件循环输出未取回异常。"""
        if task.cancelled():
            return None

        try:
            error = task.exception()
        except asyncio.CancelledError:
            return None

        if error is not None:
            logger.debug(f"[Keepalive] task stopped: {type(error).__name__}: {error}")

    @staticmethod
    async def await_cleanup(awaitable: typing.Awaitable[None]) -> None:
        """在取消态下也等待清理逻辑执行完成。"""
        task = asyncio.ensure_future(awaitable)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    def signal_processor(self, *_, **__) -> None:
        """处理终止信号，并优先触发异步清理。"""
        self.sig_count += 1
        self.task_event.set()
        self.exit_code = 130

        if self.sig_count > 1:
            sys.exit(self.exit_code)

        loop = self.runtime_loop
        if loop is not None and loop.is_running():
            self.cancel_root_task()
            return None

        sys.exit(self.exit_code)

    def cancel_root_task(self) -> None:
        """取消顶层任务，让退出沿协程栈执行清理逻辑。"""
        task = self.root_task
        if task is not None and not task.done():
            task.cancel()

    def begin_session(
        self,
        cid: typing.Optional[str] = None,
        sid: typing.Optional[str] = None,
        *,
        title: str = "",
        source: str = "begin"
    ) -> dict[str, str]:
        """初始化或续用当前会话标识。"""
        metadata = self.conversation.begin(cid=cid, sid=sid)
        self._touch_history_session(metadata, title=title, source=source)
        return metadata

    def reset_conversation(
        self,
        *,
        reason: str = "manual",
        source: str = "reset"
    ) -> dict[str, str]:
        """开始一个新的模型对话。"""
        metadata = self.conversation.reset(reason=reason)
        self._touch_history_session(metadata, source=source)
        return metadata

    def recent_conversation_sessions(
        self,
        *,
        limit: int = HISTORY_LIMIT
    ) -> list[dict[str, typing.Any]]:
        """返回当前 workspace/gravity 下可恢复的本地会话游标。"""
        try:
            records = self.history_store.list_sessions(
                workspace=self.history_workspace,
                gravity=self._history_gravity(),
                limit=limit
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            logger.debug(f"[History] list skipped: {type(exc).__name__}: {exc}")
            return []

        return [
            record for record in records
            if valid_session_ids(record.get("cid"), record.get("sid"))
        ]

    def resume_conversation(
        self,
        record: dict[str, typing.Any],
        *,
        source: str = "resume"
    ) -> typing.Optional[dict[str, str]]:
        """把当前会话绑定到 history 中选中的 cid/sid。"""
        cid = str(record.get("cid") or "").strip()
        sid = str(record.get("sid") or "").strip()
        if not valid_session_ids(cid, sid):
            logger.debug(f"[History] resume skipped: invalid cursor cid={cid} sid={sid}")
            return None

        self.conversation = ConversationState(cid=cid, sid=sid)

        metadata = self.conversation.snapshot()
        self._touch_history_session(metadata, source=source)

        return metadata

    def _touch_history_session(
        self,
        metadata: dict[str, str],
        *,
        title: str = "",
        source: str
    ) -> None:
        """把 cid/sid 写入本地 history SQLite。"""
        try:
            self.history_store.touch_session(
                cid=metadata["cid"],
                sid=metadata["sid"],
                title=title,
                workspace=self.history_workspace,
                gravity=self._history_gravity(),
                source=source
            )
        except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
            logger.debug(f"[History] write skipped: {type(exc).__name__}: {exc}")

    def set_history_workspace(self, workspace: typing.Any) -> str:
        """更新 history 使用的真实工作区根目录。"""
        normalized = normalize_workspace(workspace)
        if normalized and normalized != self.history_workspace:
            self.history_workspace = normalized
            self.native_coding = NativeCoding(root=self.history_workspace)
            self.client_tools = self._build_client_tools()
        return self.history_workspace

    def _build_client_tools(self) -> ClientToolRegistry:
        """按当前工作区构建客户端工具注册表。"""
        return default_client_tool_registry(
            self.native_coding,
            execution_root=self.history_workspace,
        )

    def _history_gravity(self) -> str:
        """返回 history 使用的归档标签。"""
        return str(self.gravity or "default")

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

    def bind_service_runtime_context(self, context: "ServiceRuntimeContext") -> None:
        """绑定服务运行时准备上下文。"""
        self.service_runtime_context = context

    def require_service_runtime_context(self) -> "ServiceRuntimeContext":
        """返回已绑定的服务运行时上下文，未绑定时抛出错误。"""
        if self.service_runtime_context is None:
            raise MindError("Service runtime context is not bound")
        return self.service_runtime_context

    def link_service_mcp(
        self,
        exec_env: typing.Optional[dict[str, typing.Any]] = None
    ) -> None:
        """把本地服务 MCP 挂入当前工具会话。"""
        self.service_mcp_linked = True

        self.service_exec_env = (
            copy.deepcopy(exec_env)
            if isinstance(exec_env, dict)
            else None
        )

    def unlink_service_mcp(self) -> None:
        """从当前工具会话移除本地服务 MCP，不停止后台进程。"""
        self.service_mcp_linked = False
        self.service_exec_env   = None

    def is_service_mcp_linked(self) -> bool:
        """判断当前工具会话是否挂载本地服务 MCP。"""
        return bool(self.service_mcp_linked)

    def service_exec_env_snapshot(self) -> dict[str, typing.Any] | None:
        """返回本地服务运行时环境快照。"""
        if not isinstance(self.service_exec_env, dict):
            return None
        return copy.deepcopy(self.service_exec_env)

    def remember_last_assistant_reply(self, text: str) -> None:
        """记录最近一次完整模型回复原文。"""
        value = str(text or "").strip()
        if value:
            self.last_assistant_reply = value

    def last_assistant_reply_snapshot(self) -> str:
        """返回最近一次完整模型回复原文。"""
        return self.last_assistant_reply

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
        self.keepalive_task.add_done_callback(self.keepalive_task_done)

    async def start_config_service(self) -> None:
        """启动 Mind 生命周期内的配置服务。"""
        await self.config_service.start()

    async def stop_config_service(self) -> None:
        """停止 Mind 生命周期内的配置服务。"""
        await self.config_service.stop()

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

    async def start_external_mcp_runtime(self, *, include_disabled: bool = False) -> None:
        """启动 Mind 生命周期级外部 MCP 运行时。"""
        if self.external_mcp is None:
            self.external_mcp = ExternalMcpRuntime(self)
        await self.external_mcp.start(include_disabled=include_disabled)

    async def restart_external_mcp_runtime(self, *, include_disabled: bool = False) -> None:
        """重启 Mind 生命周期级外部 MCP 运行时。"""
        if self.external_mcp is None:
            self.external_mcp = ExternalMcpRuntime(self)
        await self.external_mcp.restart(include_disabled=include_disabled)

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
        """关闭 Mind 持有的运行时资源，并按退出策略处理本地后台进程。"""
        await self.stop_external_mcp_runtime()
        await self.stop_config_service()
        await self.stop_keepalive_supervisor()
        server_manager = self.server_manager
        self.server_manager = None

        if server_manager is not None:
            try:
                await server_manager.close()
            finally:
                if self.stop_runtime_on_exit:
                    with contextlib.suppress(Exception):
                        await craft.kill_port(server_manager.port)

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

    async def stop_service_runtime(self) -> None:
        """停止已绑定的后台进程，并关闭对应保活任务。"""
        if self.server_manager is None:
            raise MindError("Server manager is not bound")

        self.unlink_service_mcp()
        await self.stop_keepalive_supervisor()
        await craft.kill_port(self.server_manager.port)

    async def stop_anim(self) -> None:
        """停止等待动画。"""
        await self.anim_manager.stop()

    async def start_anim(
        self,
        mode: RunMode = DEFAULT_RUN_MODE
    ) -> None:
        """启动指定模式的等待动画。"""
        if not self.animate:
            return None
        await self.anim_manager.start(
            lambda stop_event: self.design.stream_mode_live(stop_event, mode)
        )

    async def start_upload_anim(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]]
    ) -> None:
        """启动附件上传动画，并复用统一动画管理器避免冲突。"""
        if not self.animate:
            return None
        await self.anim_manager.start(
            lambda stop_event: self.design.upload_progress_live(stop_event, snapshot)
        )

    async def start_inbuild_startup_anim(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]]
    ) -> None:
        """启动内置运行时启动状态动画。"""
        if not self.animate:
            return None
        await self.anim_manager.start(
            lambda stop_event: self.design.inbuild_startup_live(stop_event, snapshot)
        )

    async def start_external_mcp_anim(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]]
    ) -> None:
        """启动外部 MCP 启动状态动画。"""
        if not self.animate:
            return None
        await self.anim_manager.start(
            lambda stop_event: self.design.external_mcp_live(stop_event, snapshot)
        )

    async def with_mcp_session(
        self,
        pref_config: dict[str, typing.Any],
        function: typing.Callable[
            [
                McpSessionLike,
                list[dict[str, typing.Any]],
            ],
            typing.Awaitable[None],
        ],
        before_user_flow: typing.Optional[typing.Callable[[], typing.Any]] = None
    ) -> None:
        """通过工具运行时建立会话并执行回调。"""
        return await self.tool_runtime.with_session(
            pref_config,
            function,
            before_user_flow=before_user_flow
        )

    async def run_mode_lifecycle(
        self,
        runner: typing.Callable[..., typing.Awaitable[None]],
        *,
        mode: RunMode = DEFAULT_RUN_MODE,
        **kwargs
    ) -> None:
        """模式执行生命周期入口：统一委托运行时模块处理动画和耗时输出。"""
        return await run_mode_lifecycle_wrapper(self, runner, mode=mode, **kwargs)

    async def calling(
        self,
        pref_config: typing.Optional[dict[str, typing.Any]] = None,
        *,
        message: str,
        mode: RunMode = DEFAULT_RUN_MODE,
        **kwargs
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
        tools: list[dict[str, typing.Any]],
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
            tools,
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
