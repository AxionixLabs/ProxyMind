# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import contextlib
from ..contracts.menu import MenuRequest
from ..contracts.resume import (
    ResumePickerRequest,
    ResumePickerResult
)
from ..contracts.text import FragmentBlock
from mind_app.frontend.contracts import ActivityStatusKind
from ..core.document import TuiBlockKind
from ..core.process_viewer import ProcessViewerRequest
from ..core.queued import TuiSubmission
from mind_core.skills import SkillSpec


class MenuSelectionPort(typing.Protocol):
    """描述只读菜单 feature 所需的菜单选择能力。"""

    async def select_menu(self, request: MenuRequest) -> typing.Any:
        """打开菜单并返回用户选择。"""
        ...


class ResumePickerPort(typing.Protocol):
    """描述 history feature 所需的全屏 Resume 选择能力。"""

    async def view_resume_picker(
        self,
        request: ResumePickerRequest,
    ) -> ResumePickerResult:
        """打开 Resume picker 并返回选择或取消。"""
        ...


class SkillInputModelPort(typing.Protocol):
    """描述 skills feature 读取的输入模型能力。"""

    @property
    def skills(self) -> tuple[SkillSpec, ...]:
        """返回当前已配置的 skill 快照。"""
        ...

    def set_skills(self, skills: typing.Iterable[SkillSpec]) -> None:
        """刷新输入补全使用的 skill 快照。"""
        ...


class SkillRuntimePort(MenuSelectionPort, typing.Protocol):
    """描述 skills feature 所需的菜单和输入能力。"""

    @property
    def input_model(self) -> SkillInputModelPort:
        """返回用于读取 skill 快照的输入模型。"""
        ...

    def replace_input_text(
        self,
        text: str,
        *,
        selected_skill: bool = False,
    ) -> None:
        """替换主输入内容并记录 skill 选择。"""
        ...

    def open_skill_search(self) -> None:
        """在主输入框中放入 `@` 并打开原生 skill 补全。"""
        ...

    def update_menu(self, request: MenuRequest) -> None:
        """刷新当前 skill 管理菜单的选项状态。"""
        ...


class TurnRuntimePort(typing.Protocol):
    """描述单轮模型执行所需的最小运行时能力。"""

    @property
    def uncertain_steers_active(self) -> bool:
        """返回当前是否存在归属未确认的输入。"""
        ...

    def request_turn_interrupt(self) -> None:
        """把当前轮次标记为用户主动中断。"""
        ...

    def consume_turn_interrupt(self) -> bool:
        """消费并返回当前轮次是否由用户主动中断。"""
        ...

    def set_execution_active(self, active: bool) -> None:
        """更新模型轮次执行状态。"""
        ...

    def clear_active_renderable(self) -> None:
        """清除已经停止的流式展示尾部。"""
        ...

    def set_turn_start_pending(self, pending: bool) -> None:
        """更新已提交但尚未开始的模型轮次状态。"""
        ...

    def bind_interrupt_handler(
        self,
        handler: typing.Callable[[], bool] | None
    ) -> None:
        """绑定或清除当前可中断生命周期的取消函数。"""
        ...

    def bind_stream_command_handler(
        self,
        handler: typing.Callable[[str], bool] | None
    ) -> None:
        """绑定或清除忙碌期间的命令分派函数。"""
        ...

    def bind_turn_input_handler(
        self,
        handler: typing.Callable[[TuiSubmission, bool], bool] | None
    ) -> None:
        """绑定或清除活动模型轮次的输入接管函数。"""
        ...

    def bind_queued_restore_handler(
        self,
        handler: typing.Callable[[TuiSubmission], None] | None
    ) -> None:
        """绑定或清除取回队列消息时的结构化草稿恢复。"""
        ...

    async def wait_for_application_failure(self) -> BaseException:
        """等待输入应用异常停止并返回原始错误。"""
        ...


class TurnInputRuntimePort(typing.Protocol):
    """描述活动轮次输入对账所需的最小运行时能力。"""

    def resolve_pending_steer(self, client_message_id: str) -> None:
        """停止展示一条已经完成归属转换的输入。"""
        ...

    def retain_uncertain_steer(self, submission: TuiSubmission) -> None:
        """保留一条不得自动重试的未确认输入。"""
        ...

    def defer_submission(self, submission: TuiSubmission) -> None:
        """把用户主动排队的输入保留到后续模型轮次。"""
        ...

    def track_pending_steer(self, submission: TuiSubmission) -> None:
        """展示一条等待当前轮次接收的输入。"""
        ...

    def discard_rejected_steer(self, client_message_id: str) -> TuiSubmission | None:
        """移除已经确认消费的即时输入重试项。"""
        ...

    def append_turn_input(
        self,
        turn_id: str,
        submission: TuiSubmission
    ) -> bool:
        """把已确认的即时输入追加到当前轮次正文并返回是否成功。"""
        ...

    def defer_rejected_steer(self, submission: TuiSubmission) -> None:
        """把未消费的即时输入保留到下一轮优先重试。"""
        ...


class ForegroundRuntimePort(typing.Protocol):
    """描述前台任务屏障所需的最小会话运行时能力。"""

    def finish_command_layout(self, *, force: bool = False) -> None:
        """结束当前命令结果交接并请求重绘。"""
        ...

    def start_background_task(
        self,
        coroutine: typing.Coroutine[typing.Any, typing.Any, None],
        *,
        name: str
    ) -> asyncio.Task[None]:
        """启动由 TUI 生命周期管理的前台后台任务。"""
        ...

    def discard_pending_submission(self) -> None:
        """清理由命令分派结束后仍未接管的暂存输入。"""
        ...

    def set_foreground_active(self, active: bool) -> None:
        """更新下一轮开始前的前台屏障状态。"""
        ...

    def bind_stream_command_handler(
        self,
        handler: typing.Callable[[str], bool] | None
    ) -> None:
        """绑定或清除忙碌期间的命令分派函数。"""
        ...

    def bind_interrupt_handler(
        self,
        handler: typing.Callable[[], bool] | None
    ) -> None:
        """绑定或清除当前可中断生命周期的取消函数。"""
        ...

    def activity_handoff(
        self,
        kind: ActivityStatusKind | None
    ) -> contextlib.AbstractContextManager[None]:
        """创建一次活动状态交接上下文。"""
        ...

    def queue_background_block(
        self,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None
    ) -> None:
        """在流式正文边界提交后台状态正文。"""
        ...


class ProcessRuntimePort(typing.Protocol):
    """描述进程 feature 在一个 TUI 会话内可使用的最小运行时能力。"""

    @property
    def inline_process_session_id(self) -> str:
        """返回当前嵌入正文的进程会话标识。"""
        ...

    @property
    def inline_process_session_ids(self) -> tuple[str, ...]:
        """返回仍由 UserShell watcher 管理的全部会话标识。"""
        ...

    @property
    def background_process_session_ids(self) -> frozenset[str]:
        """返回已切入后台的 UserShell 会话标识。"""
        ...

    @property
    def terminal_width(self) -> int:
        """返回当前终端的显示宽度。"""
        ...

    def set_process_status_label(self, label: str) -> None:
        """更新进程状态摘要行。"""
        ...

    def process_completion_snapshots(self) -> tuple[dict[str, typing.Any], ...]:
        """返回等待用户确认的进程完成快照。"""
        ...

    def acknowledge_process_completion(self, session_id: typing.Any) -> None:
        """确认并移除一项进程完成快照。"""
        ...

    def cancel_background_session_task(self, session_id: str) -> None:
        """取消指定进程会话的后台监视任务。"""
        ...

    def append_block(
        self,
        block: FragmentBlock,
        *,
        kind: TuiBlockKind = "system"
    ) -> None:
        """追加进程 feature 产生的稳定正文。"""
        ...

    def begin_process_viewer(
        self,
        request: ProcessViewerRequest,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None,
        gap_before: int | None = None
    ) -> asyncio.Future[typing.Any]:
        """激活进程查看器并返回其等待 Future。"""
        ...

    def begin_inline_process(
        self,
        session_id: str,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None,
        gap_before: int | None = None
    ) -> asyncio.Future[typing.Any]:
        """在正文中创建可更新的 Shell 执行单元。"""
        ...

    async def handoff_inline_process(self) -> None:
        """提交当前 Shell 并等待其稳定正文进入终端滚屏区。"""
        ...

    async def start_inline_process(
        self,
        session_id: str,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None,
        gap_before: int | None = None
    ) -> asyncio.Future[typing.Any]:
        """按顺序完成前一 Shell 的交接并创建新的正文执行单元。"""
        ...

    def update_inline_process(
        self,
        block: FragmentBlock,
        *,
        session_id: str | None = None,
        transcript_block: FragmentBlock | None = None,
        gap_before: int | None = None
    ) -> None:
        """更新正文中的 Shell 执行单元。"""
        ...

    def resolve_inline_process(
        self,
        value: typing.Any = None,
        *,
        session_id: str | None = None
    ) -> None:
        """提交 Shell 执行单元的动作结果。"""
        ...

    async def wait_inline_process_settled(
        self,
        session_id: str | None = None
    ) -> None:
        """等待正文中的 Shell 执行单元收束。"""
        ...

    def dismiss_inline_process(self, session_id: str | None = None) -> None:
        """清理未提交的 Shell 执行单元。"""
        ...

    def mark_inline_process_background(self, session_id: str) -> None:
        """把已切后台的 UserShell 标记为后台终端。"""
        ...

    def append_history_block(
        self,
        block: FragmentBlock,
        *,
        kind: TuiBlockKind = "operation",
        transcript_block: FragmentBlock | None = None
    ) -> None:
        """追加后台命令完成历史，不修改原稳定块。"""
        ...

    def settle_detached_inline_process(
        self,
        session_id: str
    ) -> None:
        """释放已完成后台 Shell 的运行状态。"""
        ...

    def commit_inline_process(
        self,
        block: FragmentBlock,
        *,
        session_id: str | None = None,
        transcript_block: FragmentBlock | None = None,
        retain_for_background: bool = False
    ) -> None:
        """把 Shell 执行单元提交为稳定正文。"""
        ...

    def update_process_viewer(
        self,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None,
        gap_before: int | None = None
    ) -> None:
        """更新进程查看器中的动态正文。"""
        ...

    def resolve_process_viewer(self, value: typing.Any = None) -> None:
        """提交进程查看器动作结果。"""
        ...

    def dismiss_process_viewer(self) -> None:
        """关闭进程查看器并清理动态正文。"""
        ...

    def commit_process_viewer(
        self,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None
    ) -> None:
        """把进程查看器结果提交为稳定正文。"""
        ...

    def commit_process_result(
        self,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None
    ) -> None:
        """提交已结束且属于当前会话的进程摘要。"""
        ...

    def start_background_session_task(
        self,
        session_id: str,
        coroutine: typing.Coroutine[typing.Any, typing.Any, None]
    ) -> None:
        """启动指定进程会话的唯一后台监视任务。"""
        ...

    def retain_process_completion(
        self,
        snapshot: dict[str, typing.Any],
        *,
        label: str
    ) -> None:
        """保存不属于当前会话的进程完成快照。"""
        ...

    def queue_background_block(
        self,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None
    ) -> None:
        """在流式边界提交后台进程摘要。"""
        ...

    async def select_menu(self, request: MenuRequest) -> typing.Any:
        """打开进程 feature 所需的菜单并返回用户选择。"""
        ...

    async def view_process(
        self,
        request: ProcessViewerRequest,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None,
        ready_event: asyncio.Event | None = None
    ) -> typing.Any:
        """显示进程查看器并等待用户动作。"""
        ...

    async def wait_for_process_routing_boundary(self) -> None:
        """等待命令结果路由到稳定正文的安全边界。"""
        ...


if __name__ == '__main__':
    pass
