# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import shutil
import typing
import asyncio
import contextlib
from prompt_toolkit.application import (
    Application,
    in_terminal
)
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import (
    Condition,
    has_focus,
    to_filter
)
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.input import DummyInput
from prompt_toolkit.input.base import Input
from prompt_toolkit.key_binding import (
    KeyBindings,
    merge_key_bindings
)
from prompt_toolkit.layout import (
    Dimension,
    Layout
)
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    HSplit,
    VerticalAlign,
    Window
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import (
    AfterInput,
    ConditionalProcessor
)
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.base import Output
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import (
    Style,
    merge_styles
)
from prompt_toolkit.widgets import TextArea
from mind_app.approval.models import ApprovalDecisionValue
from mind_app.frontend.contracts import (
    ActivityStatusKind,
    FrontendRuntime
)
from mind_app.interaction.contracts import PromptContext
from .models import (
    FormattedText,
    FragmentBlock,
    MenuRequest
)
from .terminal_input import clear_pending_input
from mind_nova import const
from .activity import TuiActivity
from .approval import TuiApproval
from .approval_render import TUI_APPROVAL_STYLE
from .bottom_pane import (
    BottomSurface,
    TuiBottomPane,
)
from .document import (
    TuiBlockKind,
    TuiDocument,
)
from .input import (
    INPUT_BUFFER_NAME,
    TuiInputModel
)
from .interrupt import (
    TuiExitReason,
    TuiInterruptState,
)
from .menu import (
    TUI_MENU_STYLE,
    TuiMenu
)
from .process_viewer import (
    ProcessViewerRequest,
    TuiProcessViewer,
)
from .process_status import TuiProcessStatus
from .queued import (
    TuiQueuedMessages,
    TuiSubmission
)
from .render import (
    clip_fragments,
    cursor_point,
    cursor_point_for_display_row,
    display_line_count,
    fragments_text
)
from .styles import (
    ASSISTANT_PREFIX_CLASS,
    ASSISTANT_PREFIX_STYLE,
    prompt_style,
    query_block,
)
from .task_state import TuiTaskState
from ..prompting.commands import running_disabled_commands

_QUEUE_END = object()

_RUNNING_DISABLED_COMMANDS = running_disabled_commands()


class TuiRuntime(object):
    """管理稳定画布上的会话内容、输入队列和审批交互。"""

    INPUT_MAX_LINES: typing.Final[int]           = 8
    QUEUED_MAX_HEIGHT: typing.Final[int]         = 6
    COMPLETION_MAX_HEIGHT: typing.Final[int]     = 8
    CONTENT_INPUT_GAP_HEIGHT: typing.Final[int] = 2
    OVERLAY_INPUT_GAP_HEIGHT: typing.Final[int] = 1
    COMMAND_SURFACE_GAP_HEIGHT: typing.Final[int] = 2
    FOOTER_GAP_HEIGHT: typing.Final[int]        = 1
    EXIT_CONFIRM_TIMEOUT_SEC: typing.Final[float] = 2.0
    ESCAPE_SEQUENCE_TIMEOUT_SEC: typing.Final[float] = 0.1

    def __init__(
        self,
        input_model: TuiInputModel | None = None,
        *,
        input_obj: Input | None = None,
        output_obj: Output | None = None,
    ) -> None:
        self.input_model = input_model or TuiInputModel()
        self.context = PromptContext(mode="chat", model="")
        self.placeholder_text = self.input_model.new_placeholder(self.context.mode)

        self.message_queue: asyncio.Queue[typing.Any] = asyncio.Queue()
        self.interrupt_state = TuiInterruptState(
            timeout_sec=self.EXIT_CONFIRM_TIMEOUT_SEC,
        )
        self._exit_event = asyncio.Event()
        self._exit_expiry_handle: asyncio.TimerHandle | None = None
        self._turn_interrupt_handler: typing.Callable[[], bool] | None = None
        self.task_state = TuiTaskState(
            activity_running=lambda: self.activity.active,
        )
        self._queued_submission_text: str | None = None
        self.queued_messages = TuiQueuedMessages()
        self.input_notice_blocks: list[FragmentBlock] = []
        self.input_model.bind_interrupt(self._interrupt_input)
        self.input_model.bind_exit(
            lambda: not self.execution_active,
            self._exit_input,
        )
        self.input_model.bind_queue_submission(lambda: self.execution_active)
        self.input_model.bind_queue_rollback(
            lambda: self.execution_active and self.queued_messages.active,
            self._rollback_queued_input,
        )
        self.document = TuiDocument()
        self.activity_block: FragmentBlock | None = None
        self._submitted_query_block: FragmentBlock | None = None
        self._transcript_view_row: int | None = None

        self._application_task: asyncio.Task[None] | None = None
        self._application_error: BaseException | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._background_session_tasks: dict[str, asyncio.Task[None]] = {}
        self._background_blocks: list[FragmentBlock] = []
        self._open_callbacks: list[typing.Callable[[], None]] = []
        self._closing = False

        self.activity = TuiActivity(
            set_renderable=self.set_activity_renderable,
            clear_renderable=self.clear_activity_renderable,
            get_width=lambda: self.terminal_width,
        )
        self.process_status = TuiProcessStatus(
            invalidate=self.invalidate,
            get_width=lambda: self.terminal_width,
        )

        self.input = TextArea(
            name=INPUT_BUFFER_NAME,
            multiline=True,
            lexer=self.input_model.lexer,
            auto_suggest=self.input_model.auto_suggest,
            completer=self.input_model.completer,
            complete_while_typing=Condition(
                lambda: not self.input_model.shell_mode
            ),
            accept_handler=self._accept_input,
            history=self.input_model.history,
            wrap_lines=True,
            height=self._input_dimension,
            dont_extend_height=True,
            get_line_prefix=self._input_line_prefix,
            input_processors=[
                ConditionalProcessor(
                    AfterInput(self._placeholder_fragments),
                    filter=Condition(
                        lambda: not self.input.buffer.text
                        and not self.input_model.shell_mode
                    ),
                )
            ],
        )
        self.input.buffer.enable_history_search = to_filter(True)
        self.input.buffer.on_text_changed += self._on_input_text_changed

        self.transcript_control = FormattedTextControl(
            self._transcript_fragments,
            get_cursor_position=self._transcript_cursor,
        )
        self.status_control = FormattedTextControl(self._status_fragments)
        self.process_status_control = FormattedTextControl(
            self.process_status.fragments
        )
        self.queued_control = FormattedTextControl(self._queued_fragments)
        self.footer_control = FormattedTextControl(self._footer_fragments)

        self.bottom_pane = TuiBottomPane(
            focus_surface=self._focus_bottom_surface,
            focus_input=lambda: self.application.layout.focus(self.input),
            invalidate=self.invalidate,
        )

        self.approval = TuiApproval(
            invalidate=self.invalidate,
            focus_card=lambda: self.bottom_pane.activate("approval"),
            focus_input=lambda: self.bottom_pane.deactivate("approval"),
            get_width=lambda: self.terminal_width,
            get_max_height=self._approval_available_height,
        )
        self.approval_control = FormattedTextControl(
            self.approval.fragments,
            focusable=True,
            modal=True,
            key_bindings=self.approval.key_bindings,
        )
        self.menu = TuiMenu(
            invalidate=self.invalidate,
            focus_menu=lambda: self.bottom_pane.activate("menu"),
            focus_input=lambda: self.bottom_pane.deactivate("menu"),
            get_width=lambda: self.terminal_width,
        )
        self.menu_control = FormattedTextControl(
            self.menu.fragments,
            focusable=True,
            modal=True,
            key_bindings=self.menu.key_bindings,
        )
        self.process_viewer = TuiProcessViewer(
            invalidate=self.invalidate,
            focus_viewer=lambda: self.bottom_pane.activate("process_viewer"),
            focus_input=lambda: self.bottom_pane.deactivate("process_viewer"),
            get_width=lambda: self.terminal_width,
        )
        self.process_viewer_control = FormattedTextControl(
            self.process_viewer.fragments,
            focusable=True,
            modal=True,
            key_bindings=self.process_viewer.key_bindings,
        )

        self.transcript_window = Window(
            content=self.transcript_control,
            height=self._transcript_dimension,
            wrap_lines=True,
            always_hide_cursor=True,
            dont_extend_height=True,
            get_line_prefix=self._transcript_line_prefix,
        )
        self.status_window = Window(
            content=self.status_control,
            height=self._status_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
        )
        self.process_status_window = Window(
            content=self.process_status_control,
            height=self._process_status_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
        )
        self.queued_window = Window(
            content=self.queued_control,
            height=self._queued_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
        )
        self.approval_window = Window(
            content=self.approval_control,
            height=self._approval_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
            style="class:approval-card",
            char=" ",
        )
        self.menu_window = Window(
            content=self.menu_control,
            height=self._menu_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
        )
        self.process_viewer_window = Window(
            content=self.process_viewer_control,
            height=self._process_viewer_dimension,
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
        )
        self.footer_window = Window(
            content=self.footer_control,
            height=Dimension.exact(1),
            wrap_lines=False,
            always_hide_cursor=True,
            dont_extend_height=True,
        )

        self.approval_card = ConditionalContainer(
            self.approval_window,
            filter=Condition(lambda: self.bottom_pane.is_active("approval")),
        )
        self.menu_card = ConditionalContainer(
            self.menu_window,
            filter=Condition(lambda: self.bottom_pane.is_active("menu")),
        )
        self.process_viewer_card = ConditionalContainer(
            self.process_viewer_window,
            filter=Condition(
                lambda: self.bottom_pane.is_active("process_viewer")
            ),
        )
        self.transcript_status_gap = ConditionalContainer(
            Window(height=Dimension.exact(1), char=" "),
            filter=Condition(self._transcript_status_gap_visible),
        )
        self.content_input_gap = ConditionalContainer(
            Window(height=self._content_input_gap_dimension, char=" "),
            filter=Condition(self._content_input_gap_visible),
        )
        self.completion_gap = ConditionalContainer(
            Window(
                height=Dimension.exact(1),
                char=" ",
                dont_extend_height=True,
            ),
            filter=Condition(self._completion_visible),
        )
        self.completion_menu = CompletionsMenu(
            max_height=self.COMPLETION_MAX_HEIGHT,
            scroll_offset=1,
            extra_filter=Condition(self._completion_visible),
        )
        typing.cast(Window, self.completion_menu.content).right_margins.clear()
        self.input_footer = ConditionalContainer(
            HSplit(
                [
                    Window(
                        height=Dimension.exact(self.FOOTER_GAP_HEIGHT),
                        char=" ",
                        dont_extend_height=True,
                    ),
                    self.footer_window,
                ],
                height=Dimension.exact(self.FOOTER_GAP_HEIGHT + 1),
                window_too_small=Window(),
            ),
            filter=Condition(self._footer_visible),
        )
        self.input_stack = HSplit(
            [
                self.input,
                self.completion_gap,
                self.completion_menu,
                self.input_footer,
            ],
            align=VerticalAlign.TOP,
            height=self._input_stack_dimension,
            window_too_small=Window(),
        )
        self.input_area = ConditionalContainer(
            self.input_stack,
            filter=Condition(lambda: self.bottom_pane.input_visible),
        )
        self.canvas = HSplit(
            [
                self.transcript_window,
                self.transcript_status_gap,
                self.status_window,
                self.process_status_window,
                self.queued_window,
                self.content_input_gap,
                self.process_viewer_card,
                self.menu_card,
                self.input_area,
                self.approval_card,
            ],
            align=VerticalAlign.TOP,
            height=self._canvas_dimension,
            window_too_small=Window(),
        )

        dummy_io = (
            input_obj is None
            and output_obj is None
            and not (sys.stdin.isatty() and sys.stdout.isatty())
        )
        application_input = input_obj or (DummyInput() if dummy_io else None)
        application_output = output_obj or (DummyOutput() if dummy_io else None)
        self.application: Application[None] = Application(
            layout=Layout(self.canvas, focused_element=self.input),
            key_bindings=merge_key_bindings([
                self.input_model.key_bindings,
                self._transcript_key_bindings(),
            ]),
            style=self._style(),
            full_screen=False,
            erase_when_done=False,
            mouse_support=False,
            max_render_postpone_time=None,
            input=application_input,
            output=application_output,
        )
        self.application.ttimeoutlen = self.ESCAPE_SEQUENCE_TIMEOUT_SEC

    @property
    def active(self) -> bool:
        """返回 TUI 应用是否正在运行。"""
        task = self._application_task
        return task is not None and not task.done()

    @property
    def execution_active(self) -> bool:
        """返回模型轮次是否正在运行。"""
        return self.task_state.turn_running

    @execution_active.setter
    def execution_active(self, active: bool) -> None:
        """更新模型轮次运行状态。"""
        self.task_state.set_turn_running(active)

    @property
    def task_running(self) -> bool:
        """返回模型轮次或运行期活动是否正在执行。"""
        return self.task_state.running

    @property
    def terminal_width(self) -> int:
        """返回当前渲染输出的终端列数。"""
        size = self._output_size()
        return max(20, size[0])

    @property
    def terminal_height(self) -> int:
        """返回当前渲染输出的终端行数。"""
        size = self._output_size()
        return max(1, size[1])

    async def open(self) -> None:
        """启动持久非全屏输入应用并等待首帧完成。"""
        if self.active:
            return None

        self._closing = False
        self._application_error = None
        previous_render_count = self.application.render_counter
        self._application_task = asyncio.create_task(
            self._run_application(),
            name="mind tui",
        )
        while (
            (
                not self.application.is_running
                or self.application.render_counter == previous_render_count
            )
            and not self._application_task.done()
        ):
            await asyncio.sleep(0)
        for callback in tuple(self._open_callbacks):
            callback()

    def add_open_callback(self, callback: typing.Callable[[], None]) -> None:
        """注册主应用首帧完成后的同步回调。"""
        self._open_callbacks.append(callback)
        if self.active:
            callback()

    async def close(self) -> None:
        """停止输入应用和全部动态任务。"""
        self._closing = True
        self._clear_exit_confirmation()
        self._cancel_exit_expiry()
        self.interrupt_state.clear()
        self._exit_event.clear()
        self._turn_interrupt_handler = None
        await self.activity.clear()
        self.task_state.clear()
        self.process_status.clear()
        await self.approval.close()
        await self.menu.close()
        await self.process_viewer.close()
        self.bottom_pane.clear()
        background_tasks = tuple(self._background_tasks)
        self._background_tasks.clear()
        self._background_session_tasks.clear()
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        await self._exit_application(erase=False)

    async def run_modal(
        self,
        factory: typing.Callable[[], typing.Awaitable[typing.Any]],
    ) -> typing.Any:
        """在同一个 Application 任务中暂时让出终端。"""
        context = self.application.context
        if not self.active or context is None:
            return await factory()

        async def invoke() -> typing.Any:
            async with in_terminal(render_cli_done=False):
                return await factory()

        task = context.copy().run(lambda: asyncio.create_task(invoke()))
        return await task

    async def read_message(self, context: PromptContext) -> str:
        """更新输入上下文并按提交顺序读取下一条消息。"""
        self.set_prompt_context(context)

        self._raise_requested_exit()

        queued = self.queued_messages.pop_next()
        submission = queued if queued is not None else await self._read_input_event()
        self.invalidate()
        if submission is _QUEUE_END:
            if self._application_error is not None:
                raise self._application_error
            raise EOFError
        if isinstance(submission, TuiSubmission):
            value = submission.value
            visible = submission.visible_text.strip() or value
        else:
            value = str(submission)
            visible = value

        self._clear_exit_confirmation()
        self._transcript_view_row = None
        block = query_block(visible)
        self.append_block(block, kind="user")
        self._submitted_query_block = block
        return value

    async def _read_input_event(self) -> typing.Any:
        """等待下一次输入提交，并让退出请求拥有更高优先级。"""
        submission_task = asyncio.create_task(self.message_queue.get())
        exit_task = asyncio.create_task(self._exit_event.wait())
        tasks = (submission_task, exit_task)
        done: set[asyncio.Task[typing.Any]] = set()
        try:
            done, _pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            pending = [task for task in tasks if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        if exit_task in done:
            self._raise_requested_exit()
        return submission_task.result()

    def _raise_requested_exit(self) -> None:
        """按退出来源向主交互循环传播终止信号。"""
        reason = self.consume_exit_request()
        if reason == "interrupt":
            raise KeyboardInterrupt
        if reason == "eof":
            raise EOFError

    def set_prompt_context(self, context: PromptContext) -> None:
        """在首帧或输入轮次前更新输入区展示上下文。"""
        mode_changed = context.mode != self.context.mode
        self.context = context
        self.input_model.set_mode(context.mode)
        if mode_changed:
            self.placeholder_text = self.input_model.new_placeholder(context.mode)
        self.invalidate()

    def set_process_status_label(self, label: str) -> None:
        """更新动画区域下方的后台进程摘要。"""
        self.process_status.set_label(label)

    async def request_approval(
        self,
        approval: dict[str, typing.Any],
    ) -> ApprovalDecisionValue:
        """在唯一审批区域中读取工具执行决策。"""
        wait_paused = await self.activity.pause_wait()
        try:
            return await self.approval.request(approval)
        finally:
            if wait_paused:
                await self.activity.resume_wait()

    async def select_menu(self, request: MenuRequest) -> typing.Any:
        """在主 Application 画布内读取菜单选择。"""
        self._discard_submitted_query()
        return await self.menu.request(request)

    def update_menu(self, request: MenuRequest) -> None:
        """更新主画布中的菜单或只读面板。"""
        self.menu.update(request)

    def finish_menu(self, value: typing.Any = None) -> None:
        """结束主画布中的菜单或只读面板。"""
        self.menu.finish(value)

    async def view_process(self, request: ProcessViewerRequest) -> typing.Any:
        """显示进程查看器并等待用户动作。"""
        self._discard_submitted_query()
        return await self.process_viewer.request(request)

    def update_process_viewer(self, request: ProcessViewerRequest) -> None:
        """替换当前进程查看内容。"""
        self.process_viewer.update(request)

    def finish_process_viewer(self, value: typing.Any = None) -> None:
        """结束当前进程查看器。"""
        self.process_viewer.finish(value)

    def start_background_task(
        self,
        coroutine: typing.Coroutine[typing.Any, typing.Any, None],
        *,
        name: str,
    ) -> asyncio.Task[None]:
        """启动由 TUI 生命周期管理的后台任务。"""
        task = asyncio.create_task(coroutine, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_task_done)
        return task

    def start_background_session_task(
        self,
        session_id: str,
        coroutine: typing.Coroutine[typing.Any, typing.Any, None],
    ) -> None:
        """为指定进程会话启动唯一的后台监视任务。"""
        sid = str(session_id or "").strip()
        self.cancel_background_session_task(sid)
        task = self.start_background_task(
            coroutine,
            name=f"mind process background {sid}",
        )
        self._background_session_tasks[sid] = task

    def cancel_background_session_task(self, session_id: str) -> None:
        """取消指定进程会话的后台监视任务。"""
        task = self._background_session_tasks.pop(
            str(session_id or "").strip(),
            None,
        )
        if task is not None and not task.done():
            task.cancel()

    def queue_background_block(self, block: FragmentBlock) -> None:
        """在不打断流式正文的边界提交后台摘要。"""
        if self.execution_active or self.document.active_block is not None:
            self._background_blocks.append(block)
            return None
        self.append_gap()
        self.append_block(block, kind="notice")

    def append_block(
        self,
        block: FragmentBlock,
        *,
        kind: TuiBlockKind = "system",
    ) -> None:
        """向会话内容追加一个稳定展示块。"""
        self._submitted_query_block = None
        if self.document.append_block(block, kind=kind):
            self.invalidate()

    def _discard_submitted_query(self) -> None:
        """在二级菜单接管交互时撤下刚提交的输入块。"""
        block = self._submitted_query_block
        self._submitted_query_block = None
        if block is not None and self.document.discard_trailing_block(block):
            self._transcript_view_row = None
            self.invalidate()

    def append_gap(self) -> None:
        """请求在下一项正文前保留一个视觉空行。"""
        self.document.request_gap()

    def set_active_renderable(
        self,
        block: FragmentBlock,
        *,
        kind: TuiBlockKind = "assistant",
    ) -> None:
        """替换当前流式展示块。"""
        self.document.set_active(block, kind=kind)
        self.invalidate()

    def commit_active_renderable(self, block: FragmentBlock) -> None:
        """把当前动态正文替换为同位置的稳定块。"""
        self.document.commit_active(block)
        self.invalidate()
        self._flush_background_blocks()

    def clear_active_renderable(self) -> None:
        """清空当前流式展示块。"""
        self.document.clear_active()
        self._flush_background_blocks()
        self.invalidate()

    def set_activity_renderable(self, block: FragmentBlock) -> None:
        """替换覆盖当前交互周期的活动状态内容。"""
        self.activity_block = block
        self.invalidate()

    def clear_activity_renderable(self) -> None:
        """清空覆盖当前交互周期的活动状态内容。"""
        self.activity_block = None
        self.invalidate()

    def set_execution_active(self, active: bool) -> None:
        """更新模型轮次执行状态并切换输入区布局。"""
        self.execution_active = bool(active)
        if self.execution_active:
            self._submitted_query_block = None
        else:
            self._queued_submission_text = None
            self._commit_input_notices()
            self._flush_background_blocks()
        self.invalidate()

    def bind_turn_interrupt(
        self,
        handler: typing.Callable[[], bool] | None,
    ) -> None:
        """绑定或清除当前模型轮次的取消函数。"""
        self._turn_interrupt_handler = handler

    def consume_turn_interrupt(self) -> bool:
        """消费并返回当前轮次是否由用户主动中断。"""
        return self.interrupt_state.consume_turn_interrupt()

    def consume_exit_request(self) -> TuiExitReason | None:
        """消费并返回主输入区是否已请求退出。"""
        reason = self.interrupt_state.consume_exit_request()
        if reason is not None:
            self._exit_event.clear()
        return reason

    async def begin_wait_status(self) -> None:
        """启动覆盖当前交互周期的等待动画。"""
        await self.activity.begin_wait()

    async def begin_upload_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动附件上传动画。"""
        await self.activity.begin_upload(snapshot)

    async def begin_download_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动运行时下载动画。"""
        await self.activity.begin_download(snapshot)

    async def begin_inbuild_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动内置运行时状态动画。"""
        await self.activity.begin_inbuild(snapshot)

    async def begin_external_mcp_status(
        self,
        snapshot: typing.Callable[[], dict[str, typing.Any]],
    ) -> None:
        """启动外部 MCP 状态动画。"""
        await self.activity.begin_external_mcp(snapshot)

    async def end_activity_status(
        self,
        kind: ActivityStatusKind | None = None,
    ) -> None:
        """结束运行期活动动画。"""
        await self.activity.stop(kind)

    def invalidate(self) -> None:
        """请求重新绘制当前稳定画布。"""
        if self.active and not self.application.is_done:
            with contextlib.suppress(Exception):
                self.application.invalidate()

    def _focus_bottom_surface(self, surface: BottomSurface) -> None:
        """把焦点切换到指定的底部临时交互表面。"""
        self._clear_exit_confirmation()
        controls = {
            "approval": self.approval_control,
            "menu": self.menu_control,
            "process_viewer": self.process_viewer_control,
        }
        self.application.layout.focus(controls[surface])

    async def _run_application(self) -> None:
        """运行输入应用并传播终端结束状态。"""
        try:
            with create_app_session(
                input=self.application.input,
                output=self.application.output,
            ):
                with patch_stdout(raw=True):
                    await self.application.run_async(
                        pre_run=lambda: clear_pending_input(self.application.input)
                    )
        except (EOFError, KeyboardInterrupt) as exc:
            self._application_error = exc
        except BaseException as exc:
            self._application_error = exc
        finally:
            if not self._closing:
                self.message_queue.put_nowait(_QUEUE_END)

    async def _exit_application(self, *, erase: bool) -> None:
        """结束当前应用任务并按需清除画布。"""
        task = self._application_task
        if task is None:
            return None

        self.application.erase_when_done = erase
        if not self.application.is_done:
            with contextlib.suppress(Exception):
                self.application.exit(result=None)
        with contextlib.suppress(asyncio.CancelledError, EOFError):
            await task
        self._application_task = None
        self.application.erase_when_done = False

    def _accept_input(self, buffer) -> bool:
        """恢复折叠粘贴内容并把输入追加到队列。"""
        self._clear_exit_confirmation()
        editable_text = buffer.text
        paste_store = self.input_model.submission_state()
        shell_mode = self.input_model.shell_mode
        value = self.input_model.restore_submission(buffer.text)
        if not value and not shell_mode:
            self.input_model.clear_submission_state()
            self.invalidate()
            return False
        if shell_mode:
            value = f"! {value}" if value else "!"
        command = value.casefold()
        if self.execution_active and command in _RUNNING_DISABLED_COMMANDS:
            self.input_model.rollback_submission_history(editable_text)
            self.input_notice_blocks.append(FragmentBlock((
                ("class:input.notice.marker", "■"),
                (
                    "class:input.notice",
                    f" '{command}' is disabled while a task is in progress.",
                ),
            )))
            buffer.text = ""
            buffer.cursor_position = 0
            self.input_model.clear_submission_state()
            self.invalidate()
            return False
        submission = TuiSubmission(
            value=value,
            editable_text=editable_text,
            paste_store=paste_store,
            shell_mode=shell_mode,
        )
        if self.execution_active:
            self.queued_messages.append(submission)
            self._queued_submission_text = submission.visible_text
        else:
            self.message_queue.put_nowait(submission)
        self.placeholder_text = self.input_model.new_placeholder(self.context.mode)
        buffer.text = submission.visible_text
        buffer.cursor_position = len(buffer.text)
        self.input_model.clear_submission_state()
        self.invalidate()
        return False

    def _rollback_queued_input(self) -> bool:
        """撤回最近一条待提交消息并恢复到主输入框。"""
        item = self.queued_messages.pop_last()
        if item is None:
            return False
        self.input_model.rollback_submission_history(item.visible_text)
        self._queued_submission_text = None
        self.input.buffer.text = item.editable_text
        self.input.buffer.cursor_position = len(item.editable_text)
        self.input_model.restore_submission_state(item.paste_store)
        self.input_model.set_shell_mode(item.shell_mode)
        self.invalidate()
        return True

    def _commit_input_notices(self) -> None:
        """把执行期间的输入提示按原顺序提交到正文。"""
        notices = tuple(self.input_notice_blocks)
        self.input_notice_blocks.clear()
        if not notices:
            return None
        self.append_gap()
        for block in notices:
            self.append_block(block, kind="notice")

    def _flush_background_blocks(self) -> None:
        """在流式正文结束后提交已完成的后台摘要。"""
        if self.execution_active or self.document.active_block is not None:
            return None
        blocks = tuple(self._background_blocks)
        self._background_blocks.clear()
        for block in blocks:
            self.append_gap()
            self.append_block(block, kind="notice")

    def _background_task_done(self, task: asyncio.Task[None]) -> None:
        """回收已完成的 TUI 后台任务。"""
        self._background_tasks.discard(task)
        for session_id, session_task in tuple(
            self._background_session_tasks.items()
        ):
            if session_task is task:
                self._background_session_tasks.pop(session_id, None)
        if not task.cancelled():
            task.exception()

    def _interrupt_input(self) -> None:
        """按当前交互状态处理中断或连续按键退出请求。"""
        if self.interrupt_state.exit_armed:
            self.interrupt_state.request_exit()
            self._cancel_exit_expiry()
            self._exit_event.set()
            self.invalidate()
            return None

        if not self.execution_active:
            buffer = self.input.buffer
            if buffer.text or self.input_model.shell_mode:
                buffer.text = ""
                buffer.cursor_position = 0
                self.input_model.clear_submission_state()

        self.interrupt_state.arm_exit()
        self._schedule_exit_expiry()

        handler = self._turn_interrupt_handler
        if self.execution_active and handler is not None:
            if handler():
                self.interrupt_state.request_turn_interrupt()
        self.invalidate()

    def _exit_input(self) -> None:
        """请求从空闲且空白的主输入区正常退出。"""
        self._clear_exit_confirmation()
        self.interrupt_state.request_exit("eof")
        self._exit_event.set()
        self.invalidate()

    def _schedule_exit_expiry(self) -> None:
        """安排退出确认窗口到期后的界面恢复。"""
        self._cancel_exit_expiry()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        self._exit_expiry_handle = loop.call_later(
            self.EXIT_CONFIRM_TIMEOUT_SEC,
            self._expire_exit_confirmation,
        )

    def _cancel_exit_expiry(self) -> None:
        """取消尚未触发的退出确认到期回调。"""
        handle = self._exit_expiry_handle
        self._exit_expiry_handle = None
        if handle is not None:
            handle.cancel()

    def _expire_exit_confirmation(self) -> None:
        """关闭已到期的退出确认并恢复信息栏。"""
        self._exit_expiry_handle = None
        self.interrupt_state.disarm_exit()
        self.invalidate()

    def _clear_exit_confirmation(self) -> None:
        """在用户继续交互时关闭退出确认。"""
        if not self.interrupt_state.exit_armed:
            return None
        self.interrupt_state.disarm_exit()
        self._cancel_exit_expiry()
        self.invalidate()

    def _input_line_prefix(
        self,
        line_number: int,
        wrap_count: int,
    ) -> StyleAndTextTuples:
        """生成输入首行和续行的无边框前缀。"""
        if line_number == 0 and wrap_count == 0:
            if self.input_model.shell_mode:
                return [("class:shell-escape", "! ")]
            return [("class:prompt.kicker", "> ")]
        return [("class:prompt.kicker", ". ")]

    def _placeholder_fragments(self) -> StyleAndTextTuples:
        """返回当前输入轮次固定的占位文案。"""
        return [("class:placeholder", f" {self.placeholder_text}")]

    def _on_input_text_changed(self, buffer) -> None:
        """在用户继续编辑时恢复执行期排队提示。"""
        self._clear_exit_confirmation()
        submitted_text = self._queued_submission_text
        if submitted_text is not None and buffer.text != submitted_text:
            self._queued_submission_text = None
        self.invalidate()

    def _transcript_fragments(self) -> FormattedText:
        """生成会话内容区域的格式化片段。"""
        return self.document.fragments(width=self.terminal_width)

    def _transcript_line_prefix(
        self,
        line_number: int,
        wrap_count: int,
    ) -> StyleAndTextTuples:
        """让助手正文自动折行后继续与首行正文对齐。"""
        if wrap_count <= 0 or not self._assistant_line(line_number):
            return []
        return [(ASSISTANT_PREFIX_CLASS, "  ")]

    def _assistant_line(self, target_line: int) -> bool:
        """判断指定正文逻辑行是否属于助手正文块。"""
        line_number: int    = 0
        at_line_start: bool = True

        for style, text in self._transcript_fragments():
            parts      = text.split("\n")
            last_index = len(parts) - 1

            for index, part in enumerate(parts):
                if line_number == target_line and at_line_start and part:
                    return style == ASSISTANT_PREFIX_CLASS
                if part:
                    at_line_start = False
                if index < last_index:
                    if line_number == target_line:
                        return False
                    line_number += 1
                    at_line_start = True

        return False

    def _status_fragments(self) -> FormattedText:
        """生成动画专属区域的格式化片段。"""
        block = self.activity_block
        return list(block.fragments) if block is not None else []

    def _queued_fragments(self) -> FormattedText:
        """生成动画区域下方的待提交消息。"""
        out: FormattedText = []

        for block in self.input_notice_blocks:
            if out:
                out.append(("", "\n"))
            out.extend(clip_fragments(
                list(block.fragments),
                width=self.terminal_width,
            ))

        queued = self.queued_messages.fragments(
            width=self.terminal_width,
            max_rows=self.QUEUED_MAX_HEIGHT,
        )

        if out and queued:
            out.append(("", "\n"))
        out.extend(queued)

        return out

    def _footer_fragments(self) -> FormattedText:
        """生成单行 TUI 信息栏。"""
        if self.interrupt_state.exit_armed:
            return [
                ("class:footer.exit-key", "ctrl+c"),
                ("class:footer.exit-hint", " again to exit"),
            ]
        if self._queue_submission_hint_visible():
            return [
                ("class:footer.queue-hint", "tab to queue message"),
            ]

        theme = self.input_model.theme(self.context.mode)

        parts: FormattedText = [(f"fg:{theme['brand']}", const.APP_DESC)]

        values = [
            ("class:footer.model", self.context.model or "-"),
            ("class:footer.access", self.context.access_label),
            ("class:footer.workspace", self.context.workspace_label),
        ]

        for style, value in values:
            text = str(value or "").strip()
            if text:
                parts.extend([
                    ("class:footer.separator", " · "),
                    (style, text),
                ])

        return clip_fragments(parts, width=self.terminal_width)

    def _transcript_cursor(self) -> Point:
        """让会话内容视口跟随最新输出。"""
        text = fragments_text(self._transcript_fragments())
        if self._transcript_view_row is None:
            x, y = cursor_point(text, width=self.terminal_width)
        else:
            x, y = cursor_point_for_display_row(
                text,
                width=self.terminal_width,
                display_row=self._transcript_view_row,
            )
        return Point(x=x, y=y)

    def _transcript_key_bindings(self) -> KeyBindings:
        """创建正文视口翻页按键。"""
        bindings = KeyBindings()
        input_active = has_focus(INPUT_BUFFER_NAME)

        @bindings.add("pageup", eager=True, filter=input_active)
        def _(event) -> None:
            _ = event
            self._scroll_transcript_page(-1)

        @bindings.add("pagedown", eager=True, filter=input_active)
        def _(event) -> None:
            _ = event
            self._scroll_transcript_page(1)

        return bindings

    def _scroll_transcript_page(self, direction: int) -> None:
        """按当前正文窗口高度向前或向后翻页。"""
        text = fragments_text(self._transcript_fragments())
        if not text:
            return None

        total_rows  = display_line_count(text, width=self.terminal_width)
        render_info = self.transcript_window.render_info

        window_height = (
            render_info.window_height
            if render_info is not None
            else self._transcript_dimension().preferred
        )

        if total_rows <= window_height:
            self._transcript_view_row = None
            return None

        last_row = max(0, total_rows - 1)
        current_row = (
            last_row
            if self._transcript_view_row is None
            else min(self._transcript_view_row, last_row)
        )
        page_rows  = max(1, window_height - 1)
        target_row = max(0, min(last_row, current_row + direction * page_rows))

        self._transcript_view_row = None if target_row >= last_row else target_row
        self.invalidate()

    def _canvas_dimension(self) -> Dimension:
        """返回随可见内容增长并受终端高度限制的画布高度。"""
        return Dimension.exact(self._visible_height())

    def _transcript_dimension(self) -> Dimension:
        """按剩余画布空间限制会话内容高度。"""
        text = fragments_text(self._transcript_fragments())
        rows = display_line_count(text, width=self.terminal_width)
        return Dimension.exact(min(rows, self._transcript_available_height()))

    def _transcript_available_height(self) -> int:
        """返回当前交互区域之外可分配给正文的终端行数。"""
        return max(
            0,
            self.terminal_height
            - self._interaction_height()
            - self._status_height()
            - self._process_status_height()
            - self._queued_height()
            - self._menu_height()
            - self._process_viewer_height()
            - int(self._transcript_status_gap_visible())
            - self._content_input_gap_height(),
        )

    def _status_dimension(self) -> Dimension:
        """返回动画区域的精确高度。"""
        return Dimension.exact(self._status_height())

    def _queued_dimension(self) -> Dimension:
        """返回待提交消息区域的精确高度。"""
        return Dimension.exact(self._queued_height())

    def _process_status_dimension(self) -> Dimension:
        """返回后台进程状态区域的精确高度。"""
        return Dimension.exact(self._process_status_height())

    def _input_dimension(self) -> Dimension:
        """返回输入框当前显示高度。"""
        return Dimension.exact(self._input_height())

    def _input_stack_dimension(self) -> Dimension:
        """返回输入框、补全列表和当前可见 footer 的总高度。"""
        return Dimension.exact(self._input_stack_height())

    def _approval_dimension(self) -> Dimension:
        """返回审批卡当前显示高度。"""
        return Dimension.exact(self._approval_height())

    def _menu_dimension(self) -> Dimension:
        """返回内嵌菜单当前显示高度。"""
        return Dimension.exact(self._menu_height())

    def _process_viewer_dimension(self) -> Dimension:
        """返回进程查看器当前显示高度。"""
        return Dimension.exact(self._process_viewer_height())

    def _status_height(self) -> int:
        """计算动画区域占用行数。"""
        if self.bottom_pane.is_active("approval"):
            return 0
        text = fragments_text(self._status_fragments())
        if not text:
            return 0
        return min(5, max(1, display_line_count(text, width=self.terminal_width)))

    def _queued_height(self) -> int:
        """计算待提交消息区域占用行数。"""
        if self.bottom_pane.is_active("approval"):
            return 0
        text = fragments_text(self._queued_fragments())
        if not text:
            return 0
        rows = display_line_count(text, width=self.terminal_width)
        return min(self.QUEUED_MAX_HEIGHT, max(1, rows))

    def _process_status_height(self) -> int:
        """计算后台进程状态区域占用行数。"""
        if self.bottom_pane.transient_active:
            return 0
        return 1 if self.process_status.active else 0

    def _footer_height(self) -> int:
        """返回当前输入区 footer 及其间距占用高度。"""
        return self.FOOTER_GAP_HEIGHT + 1 if self._footer_visible() else 0

    def _footer_visible(self) -> bool:
        """判断输入框下方的信息栏是否应当显示。"""
        return not self._overlay_active()

    def _queue_submission_hint_visible(self) -> bool:
        """判断执行期间是否应显示输入排队提示。"""
        return bool(
            self.execution_active
            and self.input.buffer.text != self._queued_submission_text
            and (
                self.input.buffer.text.strip()
                or self.input_model.shell_mode
            )
        )

    def _queued_content_visible(self) -> bool:
        """判断待提交区域是否存在消息或输入提示。"""
        return self.queued_messages.active or bool(self.input_notice_blocks)

    def _overlay_active(self) -> bool:
        """判断补全、选择菜单或审批层是否正在显示。"""
        return bool(
            self.bottom_pane.transient_active
            or self._completion_visible()
        )

    def _completion_visible(self) -> bool:
        """判断输入框是否存在可展示的补全候选项。"""
        state = self.input.buffer.complete_state
        return bool(
            self.bottom_pane.input_visible
            and state is not None
            and state.completions
        )

    def _completion_height(self) -> int:
        """计算无边框补全列表占用行数。"""
        if not self._completion_visible():
            return 0

        state = self.input.buffer.complete_state
        count = len(state.completions) if state is not None else 0

        available = max(1, self.terminal_height - self._input_height() - 1)

        return min(self.COMPLETION_MAX_HEIGHT, count, available)

    def _completion_section_height(self) -> int:
        """返回补全列表及其顶部间距的总高度。"""
        height = self._completion_height()
        return height + 1 if height else 0

    def _input_stack_height(self) -> int:
        """返回当前完整输入区域占用高度。"""
        return (
            self._input_height()
            + self._completion_section_height()
            + self._footer_height()
        )

    def _input_height(self) -> int:
        """计算输入内容占用的显示行数。"""
        rows = display_line_count(
            self.input.buffer.text,
            width=max(1, self.terminal_width - 2),
        )
        return max(1, min(self.INPUT_MAX_LINES, rows))

    def _approval_height(self) -> int:
        """计算审批卡在当前画布中的显示高度。"""
        if not self.bottom_pane.is_active("approval"):
            return 0

        text = fragments_text(self.approval.fragments())
        rows = display_line_count(text, width=self.terminal_width)

        return min(rows, self._approval_available_height())

    def _approval_available_height(self) -> int:
        """返回审批内容在当前终端中的可用高度。"""
        return max(
            1,
            self.terminal_height
            - self._status_height()
            - self._queued_height()
            - self._menu_height()
            - self._process_viewer_height()
            - int(self._transcript_status_gap_visible())
            - self._content_input_gap_height(),
        )

    def _menu_height(self) -> int:
        """计算内嵌菜单在当前画布中的显示高度。"""
        if not self.bottom_pane.is_active("menu"):
            return 0

        available = max(
            1,
            self.terminal_height
            - self._interaction_height()
            - self._status_height()
            - self._queued_height()
            - self._content_input_gap_height(),
        )

        return min(self.menu.height(), available)

    def _process_viewer_height(self) -> int:
        """计算进程查看器占用的高度。"""
        if not self.bottom_pane.is_active("process_viewer"):
            return 0
        available = max(
            1,
            self.terminal_height
            - self._status_height()
            - self._queued_height()
            - self._content_input_gap_height(),
        )
        return min(self.process_viewer.height(), available)

    def _interaction_height(self) -> int:
        """返回输入区或审批区当前占用的高度。"""
        if self.bottom_pane.is_active("approval"):
            return self._approval_height()
        if self.bottom_pane.transient_active:
            return 0
        return self._input_stack_height()

    def _visible_height(self) -> int:
        """计算当前画布实际可见内容的高度。"""
        height = (
            self._transcript_dimension().preferred
            + self._status_height()
            + self._process_status_height()
            + self._queued_height()
            + self._menu_height()
            + self._process_viewer_height()
            + int(self._transcript_status_gap_visible())
            + self._content_input_gap_height()
            + self._interaction_height()
        )

        return max(1, min(self.terminal_height, height))

    def _content_input_gap_height(self) -> int:
        """返回正文状态区与底部交互区域之间的间距高度。"""
        if not self._content_input_gap_visible():
            return 0
        if (
            self.bottom_pane.is_active("menu")
            or self.bottom_pane.is_active("process_viewer")
        ):
            return self.COMMAND_SURFACE_GAP_HEIGHT
        if self.bottom_pane.transient_active:
            return self.OVERLAY_INPUT_GAP_HEIGHT
        return self.CONTENT_INPUT_GAP_HEIGHT

    def _content_input_gap_dimension(self) -> Dimension:
        """返回正文状态区与底部交互区域之间的间距尺寸。"""
        return Dimension.exact(self._content_input_gap_height())

    def _content_input_gap_visible(self) -> bool:
        """判断正文状态区与底部交互区域之间是否保留空行。"""
        return bool(
            self.document.has_content
            or self.activity_block is not None
            or self.process_status.active
            or self._queued_content_visible()
        )

    def _transcript_status_gap_visible(self) -> bool:
        """判断正文与活动状态之间是否保留空行。"""
        return bool(
            self.document.has_content
            and (self._status_height() or self._process_status_height())
            and not self.bottom_pane.is_active("menu")
        )

    def _output_size(self) -> tuple[int, int]:
        """读取应用输出尺寸并提供标准终端回退值。"""
        application = getattr(self, "application", None)
        if application is not None:
            with contextlib.suppress(Exception):
                size = application.output.get_size()
                return int(size.columns), int(size.rows)

        fallback = shutil.get_terminal_size(fallback=(100, 24))
        return fallback.columns, fallback.lines

    def _style(self):
        """创建 TUI 交互区域使用的组合样式。"""
        base = self.input_model.style

        overrides = Style.from_dict({
            "assistant.prefix": prompt_style(ASSISTANT_PREFIX_STYLE),
            "auto-suggestion": "bg:default #5A616A",
            "completion-menu": "bg:default #B8C0C9",
            "completion-menu.completion": "bg:default bold #B8C0C9",
            "completion-menu.completion.current": "bg:default bold #F4F8FB",
            "completion-menu.meta.completion": "bg:default #707A84",
            "completion-menu.meta.completion.current": "bg:default #8FC7EA",
            "queue.label": "bg:default #8A929C bold",
            "queue.marker": "bg:default #7B838E",
            "queue.text": "bg:default #DDE7EF",
            "queue.more": "bg:default #7B838E",
            "input.notice.marker": "bg:default #FF5F5F bold",
            "input.notice": "bg:default #FF8A8A",
            "process-status.separator": "fg:#7B838E",
            "process-status.action": "fg:#8FC7EA bold",
            "process-status.hint": "fg:#7B838E dim",
            "footer.separator": "fg:#7B838E",
            "footer.model": "fg:#F3F5F8",
            "footer.access": "fg:#8FC7EA",
            "footer.workspace": "fg:#8A929C",
            "footer.queue-hint": "fg:#7B838E dim",
            "footer.exit-key": "fg:#FF6B6B bold",
            "footer.exit-hint": "fg:#8A929C",
            "shell.title.dot": "fg:#7F8C9A",
            "shell.title.action": "fg:#8FC7EA bold",
            "shell.title.command": "fg:#F4F7FA",
            "shell.title.suffix": "fg:#7F8C9A",
            "shell.status": "fg:#87919D",
            "shell.stdout": "fg:#D8DCE2",
            "shell.stderr": "fg:#B8C1CB",
            "ps.title": "fg:#F4F7FA bold",
            "ps.meta": "fg:#87919D",
            "ps.help": "fg:#69727D",
            "ps.output": "fg:#D8DCE2",
            "ps.waiting": "fg:#87919D",
            "ps.error": "fg:#FF6B6B",
        })

        return merge_styles([
            base,
            TUI_APPROVAL_STYLE,
            TUI_MENU_STYLE,
            overrides,
        ])


def require_tui_runtime(runtime: FrontendRuntime) -> TuiRuntime:
    """验证前端运行期为 TUI 具体实现。"""
    if not isinstance(runtime, TuiRuntime):
        raise TypeError("TUI frontend requires TuiRuntime")
    return runtime


if __name__ == '__main__':
    pass
