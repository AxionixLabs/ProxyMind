# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from prompt_toolkit.buffer import Buffer
from .input import TuiInputModel
from .interrupt import (
    TuiExitReason,
    TuiInterruptState
)
from .models import (
    FragmentBlock,
    TranscriptBacktrackRequest
)
from .queued import (
    TuiPendingSteers,
    TuiQueuedMessages,
    TuiSubmission
)
from ..prompting.commands import (
    StreamCommandPolicy,
    is_unrecognized_slash_command,
    submission_uses_transient_surface,
    stream_command_label,
    stream_command_policy,
    unrecognized_slash_command_message
)

_INPUT_CLOSED = object()


def _ignore_interrupt() -> bool:
    """忽略未绑定的中断请求。"""
    return False


def _ignore_stream_command(_command: str) -> bool:
    """忽略未绑定的流式命令。"""
    return False


def _ignore_turn_input(_submission: TuiSubmission, _queue_only: bool) -> bool:
    """忽略未绑定的活动轮次输入。"""
    return False


def _ignore_queued_restore(_submission: TuiSubmission) -> None:
    """忽略未绑定的队列草稿恢复。"""


def _no_pending_attachments() -> bool:
    """返回默认的待发送附件状态。"""
    return False


class TuiInputClosed(EOFError):
    """表示主输入 Application 已经停止。"""


class TuiInterruptRequested(Exception):
    """表示用户已确认退出当前 TUI 会话。"""


class TuiTranscriptBacktrackRequested(Exception):
    """表示完整记录请求重新编辑一条历史输入。"""

    def __init__(self, request: TranscriptBacktrackRequest) -> None:
        super().__init__(request.turn_id)
        self.request = request


class TuiSubmissionFlow(object):
    """管理输入提交、可见排队、流式命令和退出中断状态。"""

    EXIT_CONFIRM_TIMEOUT_SEC: typing.Final[float] = 2.0

    ROOT_SLASH_HINT: typing.Final[str] = (
        "Choose a slash command from the menu or type its full name."
    )

    def __init__(
        self,
        *,
        input_model: TuiInputModel,
        mode: str,
        is_submission_deferred: typing.Callable[[], bool],
        get_input_buffer: typing.Callable[[], Buffer],
        append_notice: typing.Callable[[FragmentBlock], None],
        invalidate: typing.Callable[[], None]
    ) -> None:
        self.input_model = input_model

        self.message_queue: asyncio.Queue[typing.Any] = asyncio.Queue()

        self.queued_messages = TuiQueuedMessages()
        self.pending_steers  = TuiPendingSteers()

        self.interrupt_state = TuiInterruptState(
            timeout_sec=self.EXIT_CONFIRM_TIMEOUT_SEC
        )

        self.placeholder_text = self.input_model.new_placeholder(mode)

        self.queued_submission_text: str | None = None
        self.surface_submission_pending: bool   = False
        self._queue_submission_requested: bool  = False

        self._mode = mode

        self._is_submission_deferred = is_submission_deferred
        self._get_input_buffer       = get_input_buffer
        self._append_notice          = append_notice

        self._invalidate = invalidate
        self._exit_event = asyncio.Event()

        self._exit_expiry_task: asyncio.Task[None] | None  = None
        self._interrupt_handler: typing.Callable[[], bool] = _ignore_interrupt

        self._stream_command_handler: typing.Callable[[str], bool] = (
            _ignore_stream_command
        )

        self._turn_input_handler: typing.Callable[
            [TuiSubmission, bool], bool
        ] = _ignore_turn_input

        self._queued_restore_handler: typing.Callable[[TuiSubmission], None] = (
            _ignore_queued_restore
        )

        self._has_pending_attachments: typing.Callable[[], bool] = (
            _no_pending_attachments
        )

        self.input_model.bind_interrupt(self.interrupt_input)

        self.input_model.bind_exit(
            lambda: not self._is_submission_deferred(),
            self.exit_input,
        )
        self.input_model.bind_queue_submission(
            self._is_submission_deferred,
            self.queue_input,
        )

        self.input_model.bind_queue_rollback(
            lambda: (
                self._is_submission_deferred()
                and self.queued_messages.can_rollback
            ),
            self.rollback_queued_input,
        )

    @property
    def exit_expiry_task(self) -> asyncio.Task[None] | None:
        """返回当前退出确认到期任务。"""
        return self._exit_expiry_task

    @property
    def has_pending_attachments(self) -> bool:
        """返回当前是否存在可随空消息发送的附件。"""
        return bool(self._has_pending_attachments())

    def _reject_input(
        self,
        buffer: Buffer,
        *,
        editable_text: str,
        message: str
    ) -> bool:
        """拒绝无效输入并显示一项输入提示。"""
        self.input_model.rollback_submission_history(editable_text)
        self.input_model.clear_submission_state()

        buffer.text = ""
        buffer.cursor_position = 0

        self._append_notice(FragmentBlock((
            ("class:input.notice.hint", "• "),
            ("class:input.notice.hint", message),
        )))
        self._invalidate()
        return False

    def _schedule_exit_expiry(self) -> None:
        """安排退出确认窗口到期后的界面恢复。"""
        self._cancel_exit_expiry()
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return None

        self._exit_expiry_task = asyncio.create_task(
            self._expire_exit_confirmation(),
            name="tui exit confirmation expiry",
        )

    def _cancel_exit_expiry(self) -> None:
        """取消尚未完成的退出确认到期任务。"""
        task = self._exit_expiry_task
        self._exit_expiry_task = None
        if task is not None and not task.done():
            task.cancel()

    def _raise_requested_exit(self) -> None:
        """按退出来源传播终止信号。"""
        reason = self.consume_exit_request()
        if reason == "interrupt":
            raise TuiInterruptRequested
        if reason == "eof":
            raise EOFError

    def _dispatch_stream_command(
        self,
        submission: TuiSubmission,
        *,
        policy: StreamCommandPolicy
    ) -> None:
        """按流式期间策略分派命令或生成拒绝提示。"""
        handled = bool(
            policy != "reject"
            and self._stream_command_handler(submission.value)
        )
        if handled:
            return None

        label = stream_command_label(submission.value)

        self.input_model.rollback_submission_history(submission.editable_text)

        self._append_notice(FragmentBlock((
            ("class:input.notice.marker", "■"),
            ("class:input.notice", " '"),
            (
                "class:prompt.command.slash"
                if label.startswith("/")
                else "class:input.notice",
                label,
            ),
            (
                "class:input.notice",
                "' is disabled while a task is in progress.",
            ),
        )))

    def enqueue_message(
        self,
        value: str,
        *,
        visible_text: str | None = None
    ) -> None:
        """把外部提交的文本写入消息队列。"""
        self.surface_submission_pending = submission_uses_transient_surface(value)
        self.message_queue.put_nowait(TuiSubmission(
            value=value,
            editable_text=value if visible_text is None else visible_text,
            paste_store={},
        ))

    def defer_submission(
        self,
        submission: TuiSubmission,
        *,
        next_input: bool = False,
    ) -> None:
        """把输入保留到本轮结束后，并按结算优先级排队。"""
        if next_input:
            self.queued_messages.remove(submission.client_message_id)
            self.queued_messages.append_next(submission)
        else:
            self.queued_messages.append(submission)
        self._invalidate()

    def track_pending_steer(self, submission: TuiSubmission) -> None:
        """展示一条等待写入当前轮次的输入。"""
        self.pending_steers.add(submission)
        self._invalidate()

    def resolve_pending_steer(self, client_message_id: str) -> None:
        """停止展示一条已经确认或转入下一轮的输入。"""
        if self.pending_steers.remove(client_message_id) is not None:
            self._invalidate()

    def queue_input(self, buffer: Buffer) -> None:
        """使用下一轮意图提交当前输入内容。"""
        self._queue_submission_requested = True
        try:
            buffer.validate_and_handle()
        finally:
            self._queue_submission_requested = False

    def finish_input(self) -> None:
        """通知等待方主输入应用已经停止。"""
        self.surface_submission_pending = False
        self.message_queue.put_nowait(_INPUT_CLOSED)

    def enqueue_transcript_backtrack(
        self,
        request: TranscriptBacktrackRequest
    ) -> None:
        """把完整记录中的历史编辑请求写入输入事件队列。"""
        self.message_queue.put_nowait(request)

    def clear_surface_submission_pending(self) -> None:
        """清除等待临时交互表面接管的提交标记。"""
        self.surface_submission_pending = False

    def rollback_queued_input(self) -> bool:
        """撤回最近一条待提交消息并恢复到主输入框。"""
        item = self.queued_messages.pop_last()
        if item is None:
            return False

        self._queued_restore_handler(item)

        self.input_model.rollback_submission_history(item.visible_text)

        self.queued_submission_text = None

        buffer = self._get_input_buffer()
        buffer.text = item.editable_text
        buffer.cursor_position = len(item.editable_text)

        self.input_model.restore_submission_state(item.paste_store)
        self.input_model.set_shell_mode(item.shell_mode)
        self._invalidate()

        return True

    def on_input_text_changed(self, buffer: Buffer) -> None:
        """在用户继续编辑时恢复执行期排队提示。"""
        self.clear_exit_confirmation()
        self.input_model.cancel_history_backtrack()
        submitted_text = self.queued_submission_text
        if submitted_text is not None and buffer.text != submitted_text:
            self.queued_submission_text = None
        self._invalidate()

    def clear_queued_submission_marker(self) -> None:
        """清除刚提交消息在输入框中的临时标记。"""
        self.queued_submission_text = None

    def bind_interrupt_handler(
        self,
        handler: typing.Callable[[], bool] | None
    ) -> None:
        """绑定或清除当前可中断生命周期的取消函数。"""
        self._interrupt_handler = (
            handler if handler is not None else _ignore_interrupt
        )

    def bind_stream_command_handler(
        self,
        handler: typing.Callable[[str], bool] | None
    ) -> None:
        """绑定或清除忙碌期间的命令分派函数。"""
        self._stream_command_handler = (
            handler if handler is not None else _ignore_stream_command
        )

    def bind_turn_input_handler(
        self,
        handler: typing.Callable[[TuiSubmission, bool], bool] | None,
    ) -> None:
        """绑定或清除活动模型轮次的输入接管函数。"""
        self._turn_input_handler = (
            handler if handler is not None else _ignore_turn_input
        )

    def bind_queued_restore_handler(
        self,
        handler: typing.Callable[[TuiSubmission], None] | None
    ) -> None:
        """绑定或清除队列消息取回时的结构化草稿恢复。"""
        self._queued_restore_handler = (
            handler if handler is not None else _ignore_queued_restore
        )

    def request_turn_interrupt(self) -> None:
        """把当前轮次标记为用户主动中断。"""
        self.interrupt_state.request_turn_interrupt()

    def consume_turn_interrupt(self) -> bool:
        """消费并返回当前轮次是否由用户主动中断。"""
        return self.interrupt_state.consume_turn_interrupt()

    def consume_exit_request(self) -> TuiExitReason | None:
        """消费并返回主输入区是否已请求退出。"""
        reason = self.interrupt_state.consume_exit_request()
        if reason is not None:
            self._exit_event.clear()
        return reason

    def interrupt_input(self) -> None:
        """按当前交互状态处理中断或连续按键退出请求。"""
        self.input_model.cancel_history_backtrack()
        if self.interrupt_state.exit_armed:
            self.interrupt_state.request_exit()
            self._cancel_exit_expiry()
            self._exit_event.set()
            self._invalidate()
            return None

        if not self._is_submission_deferred():
            buffer = self._get_input_buffer()
            if buffer.text or self.input_model.shell_mode:
                buffer.text = ""
                buffer.cursor_position = 0
                self.input_model.clear_submission_state()

        self.interrupt_state.arm_exit()
        self._schedule_exit_expiry()

        self._interrupt_handler()
        self._invalidate()

    def exit_input(self) -> None:
        """请求从空闲且空白的主输入区正常退出。"""
        self.clear_exit_confirmation()
        self.interrupt_state.request_exit("eof")
        self._exit_event.set()
        self._invalidate()

    def clear_exit_confirmation(self) -> None:
        """在用户继续交互时关闭退出确认。"""
        if not self.interrupt_state.exit_armed:
            return None
        self.interrupt_state.disarm_exit()
        self._cancel_exit_expiry()
        self._invalidate()

    def set_mode(self, mode: str) -> None:
        """更新输入建议模式并在模式变化时刷新占位文案。"""
        mode_changed = mode != self._mode

        self._mode = mode

        self.input_model.set_mode(mode)
        if mode_changed:
            self.placeholder_text = self.input_model.new_placeholder(mode)
        self._invalidate()

    def accept_input(self, buffer: Buffer) -> bool:
        """恢复折叠粘贴内容并按当前运行状态提交输入。"""
        self.clear_exit_confirmation()
        self.input_model.cancel_history_backtrack()

        editable_text = buffer.text
        paste_store   = self.input_model.submission_state()
        shell_mode    = self.input_model.shell_mode

        value = self.input_model.restore_submission(buffer.text)
        if not value:
            if shell_mode:
                return False

            if not self.has_pending_attachments:
                return False

        if shell_mode:
            value = f"! {value}" if value else "!"

        if value == "/":
            return self._reject_input(
                buffer,
                editable_text=editable_text,
                message=self.ROOT_SLASH_HINT,
            )

        if is_unrecognized_slash_command(value):
            self._append_notice(FragmentBlock((
                ("class:input.notice.hint", "•"),
                (
                    "class:input.notice.hint",
                    f" {unrecognized_slash_command_message(value)}",
                ),
            )))
            self._invalidate()
            return True

        submission = TuiSubmission(
            value=value,
            editable_text=editable_text,
            paste_store=paste_store,
            shell_mode=shell_mode,
        )

        if self._is_submission_deferred():
            policy = stream_command_policy(value)
            if policy is not None:
                self._dispatch_stream_command(submission, policy=policy)
                buffer.text = ""
                buffer.cursor_position = 0
                self.input_model.clear_submission_state()
                self._invalidate()
                return False

            handled = self._turn_input_handler(
                submission,
                self._queue_submission_requested,
            )

            if not handled:
                self.defer_submission(submission)
            self.queued_submission_text = submission.visible_text

        else:
            self.surface_submission_pending = submission_uses_transient_surface(
                submission.value
            )
            self.message_queue.put_nowait(submission)

        self.placeholder_text = self.input_model.new_placeholder(self._mode)

        buffer.text = submission.visible_text
        buffer.cursor_position = len(buffer.text)

        self.input_model.clear_submission_state()
        self._invalidate()

        return False

    def bind_pending_attachment_check(
        self,
        check: typing.Callable[[], bool] | None
    ) -> None:
        """绑定或清除待发送附件状态判断。"""
        self._has_pending_attachments = (
            check if check is not None else _no_pending_attachments
        )

    async def _read_input_event(self) -> typing.Any:
        """等待输入或退出事件，并取消未完成的另一项等待。"""
        submission_task = asyncio.create_task(self.message_queue.get())
        exit_task       = asyncio.create_task(self._exit_event.wait())
        tasks           = (submission_task, exit_task)

        try:
            finished, _ = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            pending = [task for task in tasks if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        if exit_task in finished:
            self._raise_requested_exit()

        return submission_task.result()

    async def _expire_exit_confirmation(self) -> None:
        """关闭已到期的退出确认并恢复信息栏。"""
        await asyncio.sleep(self.interrupt_state.timeout_sec)
        self._exit_expiry_task = None
        self.interrupt_state.disarm_exit()
        self._invalidate()

    async def read_submission(self) -> typing.Any:
        """按提交顺序读取下一项输入，并优先传播退出请求。"""
        self._raise_requested_exit()

        queued     = self.queued_messages.pop_next()
        submission = queued if queued is not None else await self._read_input_event()

        self._invalidate()

        if submission is _INPUT_CLOSED:
            raise TuiInputClosed

        self.clear_exit_confirmation()
        return submission

    async def close(self) -> None:
        """清理提交状态和退出确认任务。"""
        task = self._exit_expiry_task

        self.interrupt_state.disarm_exit()
        self._cancel_exit_expiry()

        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

        self.interrupt_state.clear()
        self._exit_event.clear()

        self._interrupt_handler       = _ignore_interrupt
        self._stream_command_handler  = _ignore_stream_command
        self._turn_input_handler      = _ignore_turn_input
        self._queued_restore_handler  = _ignore_queued_restore
        self._has_pending_attachments = _no_pending_attachments


if __name__ == '__main__':
    pass
