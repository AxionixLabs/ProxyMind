# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import inspect
import time
import typing

from agent.ports.frontend import ActivityStatusKind
from infrastructure.errors import AppError
from infrastructure.services.runtime_setup import service_runtime_asset_missing
from observability import (
    observe,
    observe_exception
)
from ..core.interrupt import InterruptDisposition
from ..core.styles import (
    MUTED_STYLE,
    text_block
)
from ..features.helix import (
    link_helix_runtime,
    render_helix_interrupted,
    render_helix_link_failure,
    render_helix_link_result
)
from ..features.listener import (
    ListenerOperation,
    render_listener_failure,
    render_listener_interrupted,
    render_listener_result,
    run_listener_action
)
from ..features.mcp import (
    McpAction,
    parse_mcp_command,
    render_mcp_action_cancelled,
    render_mcp_action_failure,
    render_mcp_action_result,
    run_mcp_action
)
from ..prompting.commands import matches_command
from ..runtime.ports import ForegroundRuntimePort

if typing.TYPE_CHECKING:
    from ..application import TuiApplicationHost

CancelCleanup = typing.Callable[[], typing.Awaitable[None]]

SucceededHandler = typing.Callable[
    [typing.Any],
    typing.Awaitable[None] | None
]

FailedHandler = typing.Callable[[BaseException], None]
CancelledHandler = typing.Callable[[], None]


class TuiForegroundTasks(object):
    """管理可取消前台任务及下一轮模型调用屏障。"""

    def __init__(self, runtime: ForegroundRuntimePort, mind: "TuiApplicationHost") -> None:
        self.runtime = runtime
        self.mind = mind

        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start(
        self,
        key: str,
        factory: typing.Callable[
            [],
            typing.Coroutine[typing.Any, typing.Any, typing.Any],
        ],
        *,
        cancel_cleanup: CancelCleanup | None = None,
        activity_kind: ActivityStatusKind | None = None,
        on_succeeded: SucceededHandler | None = None,
        on_failed: FailedHandler | None = None,
        on_cancelled: CancelledHandler | None = None
    ) -> bool:
        """合并同类前台任务并注册下一轮屏障。"""
        self.runtime.finish_command_layout(force=True)

        active: asyncio.Task[None] | None = self._tasks.get(key)
        if active is not None and not active.done():
            observe("operation.skipped", operation=key, reason="already_running")
            self._defer_notice(f"{key} startup is already in progress.")
            return True

        task = self.runtime.start_background_task(
            self._run_operation(
                key,
                factory,
                cancel_cleanup=cancel_cleanup,
                activity_kind=activity_kind,
                on_succeeded=on_succeeded,
                on_failed=on_failed,
                on_cancelled=on_cancelled,
            ),
            name=f"tui foreground {key}",
        )
        self._tasks[key] = task
        task.add_done_callback(
            lambda completed: self._forget(key, completed)
        )
        return True

    def cancel(self) -> bool:
        """取消尚未完成的前台任务。"""
        cancelled = False
        for task in self._task_snapshot():
            if not task.done():
                cancelled = task.cancel() or cancelled
        return cancelled

    def handle_interrupt(self) -> InterruptDisposition:
        """处理中断手势并返回前台任务是否已消费。"""
        if self.cancel():
            return InterruptDisposition.CONSUMED
        return InterruptDisposition.IGNORED

    def start_helix_link(self) -> bool:
        """按统一生命周期启动 Helix 接入任务。"""
        return self.start(
            "Helix MCP",
            lambda: link_helix_runtime(self.mind),
            cancel_cleanup=self.mind.service_runtime.cancel_startup,
            activity_kind="inbuild",
            on_succeeded=lambda linked: render_helix_link_result(
                self.mind,
                linked,
            ),
            on_failed=lambda error: render_helix_link_failure(
                self.mind,
                error,
            ),
            on_cancelled=lambda: render_helix_interrupted(self.mind),
        )

    def start_external_mcp(self, action: McpAction) -> bool:
        """按统一生命周期启动外部 MCP 任务。"""
        activity_kind: ActivityStatusKind
        if action == "stop":
            activity_kind = "operation"
        else:
            activity_kind = "external_mcp"

        return self.start(
            "External MCP",
            lambda: run_mcp_action(self.mind, action),
            activity_kind=activity_kind,
            on_succeeded=lambda was_started: render_mcp_action_result(
                self.mind,
                action,
                was_started,
            ),
            on_failed=lambda error: render_mcp_action_failure(
                self.mind,
                action,
                error,
            ),
            on_cancelled=lambda: render_mcp_action_cancelled(
                self.mind,
                action,
            ),
        )

    def start_listener(
        self,
        action: ListenerOperation,
        *,
        on_succeeded: typing.Callable[[], None] | None = None,
    ) -> bool:
        """按统一生命周期启动监听器状态切换任务。"""

        def finish(outcome: typing.Any) -> None:
            """展示监听器结果并通知会话级依赖刷新绑定。"""
            render_listener_result(self.mind, outcome)
            if on_succeeded is not None:
                on_succeeded()

        return self.start(
            "Listener",
            lambda: run_listener_action(self.mind, action),
            cancel_cleanup=(
                self.mind.subscription.pause
                if action == "start"
                else None
            ),
            activity_kind="operation",
            on_succeeded=finish,
            on_failed=lambda error: render_listener_failure(
                self.mind,
                action,
                error,
            ),
            on_cancelled=lambda: render_listener_interrupted(
                self.mind,
                action,
            ),
        )

    def handle_stream_command(
        self,
        value: str,
        cancel_turn: typing.Callable[[], InterruptDisposition],
    ) -> bool:
        """分派允许在模型流式输出期间执行的命令。"""
        command = str(value or "").strip().casefold()

        if matches_command(command, "quit"):
            self.mind.lifecycle.request_stop()
            self.cancel()
            cancel_turn()
            return True
        if matches_command(command, "shutdown"):
            self.mind.service_runtime.request_termination_on_close()
            self.mind.lifecycle.request_stop()
            self.cancel()
            cancel_turn()
            return True
        if matches_command(command, "helix_link"):
            if self.mind.execution.is_service_linked():
                self._defer_notice("Helix MCP is already linked.")
                return True
            try:
                context = self.mind.service_runtime.require_context()
            except AppError:
                return False
            if service_runtime_asset_missing(context):
                return False

            return self.start_helix_link()

        is_mcp, mcp_action = parse_mcp_command(command)

        if not is_mcp or mcp_action is None:
            return False
        if mcp_action not in {"start", "force"}:
            return False

        external_mcp = self.mind.execution.external_mcp.current

        if external_mcp is not None and external_mcp.started:
            if mcp_action == "start":
                self._defer_notice("External MCP is already started.")
                return True
            return False

        return self.start_external_mcp(mcp_action)

    async def wait(self) -> None:
        """持续等待派生前台任务稳定结束后再允许下一次模型调用。"""
        pending = tuple(
            task
            for task in self._task_snapshot()
            if not task.done()
        )
        if not pending:
            return None

        self.runtime.discard_pending_submission()
        self.runtime.set_foreground_active(True)
        self.runtime.bind_stream_command_handler(self._handle_wait_command)
        self.runtime.bind_interrupt_handler(self.handle_interrupt)

        try:
            while pending:
                await asyncio.gather(*pending, return_exceptions=True)
                await asyncio.sleep(0)
                pending = tuple(
                    task
                    for task in self._task_snapshot()
                    if not task.done()
                )
        finally:
            self.runtime.bind_interrupt_handler(None)
            self.runtime.bind_stream_command_handler(None)
            self.runtime.set_foreground_active(False)

    async def _run_operation(
        self,
        key: str,
        factory: typing.Callable[
            [],
            typing.Coroutine[typing.Any, typing.Any, typing.Any],
        ],
        *,
        cancel_cleanup: CancelCleanup | None,
        activity_kind: ActivityStatusKind | None,
        on_succeeded: SucceededHandler | None,
        on_failed: FailedHandler | None,
        on_cancelled: CancelledHandler | None
    ) -> None:
        """执行前台任务并按统一顺序完成活动状态和结果提交。"""
        started_at = time.perf_counter()

        observe("operation.start", operation=key)

        try:
            result = await factory()
        except asyncio.CancelledError:
            try:
                if cancel_cleanup is not None:
                    await self.mind.lifecycle.await_cleanup(cancel_cleanup())
            finally:
                with self.runtime.activity_handoff(activity_kind):
                    if on_cancelled is not None:
                        on_cancelled()

            observe(
                "operation.interrupted",
                level="WARNING",
                operation=key,
                elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            )
            raise

        except AppError as error:
            observe_exception(
                "operation.failed",
                error,
                operation=key,
                elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            )

            if on_failed is None:
                with self.runtime.activity_handoff(activity_kind):
                    pass
                raise
            with self.runtime.activity_handoff(activity_kind):
                on_failed(error)

        except Exception as error:
            observe_exception(
                "operation.failed",
                error,
                operation=key,
                elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            )

            if on_failed is None:
                with self.runtime.activity_handoff(activity_kind):
                    pass
                raise
            with self.runtime.activity_handoff(activity_kind):
                on_failed(error)

        else:
            with self.runtime.activity_handoff(activity_kind):
                if on_succeeded is not None:
                    handled = on_succeeded(result)
                    if inspect.isawaitable(handled):
                        await handled
            observe(
                "operation.complete",
                operation=key,
                elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            )

    def _handle_wait_command(self, value: str) -> bool:
        """在后台屏障等待期间只处理退出类命令。"""
        command = str(value or "").strip().casefold()

        if matches_command(command, "quit"):
            self.mind.lifecycle.request_stop()
        elif matches_command(command, "shutdown"):
            self.mind.service_runtime.request_termination_on_close()
            self.mind.lifecycle.request_stop()
        else:
            return False

        self.cancel()
        return True

    def _defer_notice(self, message: str) -> None:
        """在当前流式正文结束后展示命令状态。"""
        asyncio.get_running_loop().call_soon(
            self.runtime.queue_background_block,
            text_block(message, MUTED_STYLE),
        )

    def _task_snapshot(self) -> tuple[asyncio.Task[None], ...]:
        """返回不会受完成回调修改影响的前台任务快照。"""
        tasks: list[asyncio.Task[None]] = []
        for key in self._tasks:
            tasks.append(self._tasks[key])
        return tuple(tasks)

    def _forget(self, key: str, completed: asyncio.Task[None]) -> None:
        """移除已经完成的同类启动屏障。"""
        if self._tasks.get(key) is completed:
            self._tasks.pop(key, None)


if __name__ == '__main__':
    pass
