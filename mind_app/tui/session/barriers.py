# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from engine.errors import MindError
from mind_app.runtime.mcp.service_runtime import service_runtime_asset_missing
from ..core.runtime import TuiRuntime
from ..core.styles import (
    MUTED_STYLE,
    text_block
)
from ..features.helix import (
    finish_helix_activity,
    link_helix_runtime,
    render_helix_interrupted,
    render_helix_link_failure,
    render_helix_link_result
)
from ..features.mcp import (
    McpAction,
    finish_mcp_activity,
    parse_mcp_command,
    render_mcp_action_cancelled,
    render_mcp_action_failure,
    render_mcp_action_result,
    run_mcp_action
)
from ..prompting.commands import matches_command

if typing.TYPE_CHECKING:
    from ...controller import Mind

CancelCleanup    = typing.Callable[[], typing.Awaitable[None]]
ActivityFinisher = typing.Callable[[], typing.Awaitable[None]]
SucceededHandler = typing.Callable[[typing.Any], None]
FailedHandler    = typing.Callable[[BaseException], None]
CancelledHandler = typing.Callable[[], None]


class TuiForegroundTasks(object):
    """管理可取消前台任务及下一轮模型调用屏障。"""

    def __init__(self, runtime: TuiRuntime, mind: "Mind") -> None:
        self.runtime = runtime
        self.mind    = mind

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
        finish_activity: ActivityFinisher | None = None,
        on_succeeded: SucceededHandler | None = None,
        on_failed: FailedHandler | None = None,
        on_cancelled: CancelledHandler | None = None
    ) -> bool:
        """合并同类前台任务并注册下一轮屏障。"""
        active: asyncio.Task[None] | None = self._tasks.get(key)
        if active is not None and not active.done():
            self._defer_notice(f"{key} startup is already in progress.")
            return True

        task = self.runtime.start_background_task(
            self._run_operation(
                factory,
                cancel_cleanup=cancel_cleanup,
                finish_activity=finish_activity,
                on_succeeded=on_succeeded,
                on_failed=on_failed,
                on_cancelled=on_cancelled,
            ),
            name=f"mind tui foreground {key}",
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

    def start_helix_link(self) -> bool:
        """按统一生命周期启动 Helix 接入任务。"""
        return self.start(
            "Helix MCP",
            lambda: link_helix_runtime(self.mind),
            cancel_cleanup=self.mind.cancel_service_runtime_startup,
            finish_activity=lambda: finish_helix_activity(self.mind),
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
        return self.start(
            "External MCP",
            lambda: run_mcp_action(self.mind, action),
            finish_activity=lambda: finish_mcp_activity(
                self.mind,
                action,
            ),
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

    def handle_stream_command(
        self,
        value: str,
        cancel_turn: typing.Callable[[], bool],
    ) -> bool:
        """分派允许在模型流式输出期间执行的命令。"""
        command = str(value or "").strip().casefold()

        if matches_command(command, "quit"):
            self.mind.task_event.set()
            self.cancel()
            cancel_turn()
            return True
        if matches_command(command, "shutdown"):
            self.mind.stop_runtime_on_exit = True
            self.mind.task_event.set()
            self.cancel()
            cancel_turn()
            return True
        if matches_command(command, "helix_link"):
            if self.mind.is_service_mcp_linked():
                self._defer_notice("Helix MCP is already linked.")
                return True
            try:
                context = self.mind.require_service_runtime_context()
            except MindError:
                return False
            if service_runtime_asset_missing(context):
                return False

            return self.start_helix_link()

        is_mcp, mcp_action = parse_mcp_command(command)

        if not is_mcp or mcp_action not in {"start", "force"}:
            return False

        external_mcp = getattr(self.mind, "external_mcp", None)

        if bool(getattr(external_mcp, "started", False)):
            if mcp_action == "start":
                self._defer_notice("External MCP is already started.")
                return True
            return False

        return self.start_external_mcp(mcp_action)

    async def wait(self) -> None:
        """等待后台启动完成后再允许下一次模型调用。"""
        pending = tuple(
            task
            for task in self._task_snapshot()
            if not task.done()
        )
        if not pending:
            return None

        self.runtime.set_foreground_active(True)
        self.runtime.bind_stream_command_handler(self._handle_wait_command)
        self.runtime.bind_interrupt_handler(self.cancel)

        try:
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            self.runtime.bind_interrupt_handler(None)
            self.runtime.bind_stream_command_handler(None)
            self.runtime.set_foreground_active(False)

    async def _run_operation(
        self,
        factory: typing.Callable[
            [],
            typing.Coroutine[typing.Any, typing.Any, typing.Any],
        ],
        *,
        cancel_cleanup: CancelCleanup | None,
        finish_activity: ActivityFinisher | None,
        on_succeeded: SucceededHandler | None,
        on_failed: FailedHandler | None,
        on_cancelled: CancelledHandler | None
    ) -> None:
        """执行前台任务并按统一顺序完成活动状态和结果提交。"""
        try:
            result = await factory()
            if finish_activity is not None:
                await finish_activity()
        except asyncio.CancelledError:
            if cancel_cleanup is not None:
                await self.mind.await_cleanup(cancel_cleanup())
            if finish_activity is not None:
                await self.mind.await_cleanup(finish_activity())
            if on_cancelled is not None:
                on_cancelled()
            raise
        except MindError as error:
            if finish_activity is not None:
                await self.mind.await_cleanup(finish_activity())
            if on_failed is None:
                raise
            on_failed(error)
        except Exception as error:
            if finish_activity is not None:
                await self.mind.await_cleanup(finish_activity())
            if on_failed is None:
                raise
            on_failed(error)
        else:
            if on_succeeded is not None:
                on_succeeded(result)

    def _handle_wait_command(self, value: str) -> bool:
        """在后台屏障等待期间只处理退出类命令。"""
        command = str(value or "").strip().casefold()

        if matches_command(command, "quit"):
            self.mind.task_event.set()
        elif matches_command(command, "shutdown"):
            self.mind.stop_runtime_on_exit = True
            self.mind.task_event.set()
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
