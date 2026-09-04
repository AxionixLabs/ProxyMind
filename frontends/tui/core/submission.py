# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing

from prompt_toolkit.buffer import Buffer

from .input import TuiInputModel
from .interrupt import (
    InterruptDisposition,
    TuiExitReason,
    TuiInterruptState
)
from .models import (
    FragmentBlock,
    MailboxRunRequest,
    TranscriptBacktrackRequest
)
from .queued import (
    TuiPendingSteers,
    TuiQueuedMessages,
    TuiRejectedSteers,
    TuiSubmission
)
from ..prompting.commands import (
    StreamCommandPolicy,
    slash_command_notice_message,
    submission_uses_transient_surface,
    stream_command_label,
    stream_command_policy
)
from ..prompting.paste import (
    format_paste_placeholder,
    iter_paste_placeholders,
    parse_paste_placeholder,
)

_INPUT_CLOSED = object()


def _ignore_interrupt() -> InterruptDisposition:
    """忽略未绑定的中断请求。"""
    return InterruptDisposition.IGNORED


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


class TuiMailboxRunRequested(Exception):
    """通知会话循环执行一条收件箱消息。"""

    def __init__(self, request: MailboxRunRequest) -> None:
        super().__init__(request.message_id)
        self.request = request


class TuiSubmissionFlow(object):
    """管理输入提交、可见排队、流式命令和退出中断状态。"""

    EXIT_CONFIRM_TIMEOUT_SEC: typing.Final[float] = 2.0

    def __init__(
        self,
        *,
        input_model: TuiInputModel,
        is_submission_deferred: typing.Callable[[], bool],
        get_input_buffer: typing.Callable[[], Buffer],
        append_notice: typing.Callable[[FragmentBlock], None],
        invalidate: typing.Callable[[], None]
    ) -> None:
        self.input_model = input_model
        self.message_queue: asyncio.Queue[typing.Any] = asyncio.Queue()
        self.queued_messages = TuiQueuedMessages()
        self.pending_steers = TuiPendingSteers()
        self.rejected_steers = TuiRejectedSteers()
        self.interrupt_state = TuiInterruptState(
            timeout_sec=self.EXIT_CONFIRM_TIMEOUT_SEC
        )
        self.placeholder_text = self.input_model.new_placeholder()
        self.queued_submission_text: str | None = None
        self.surface_submission_pending: bool = False
        self._queue_submission_requested: bool = False
        self._input_handoff_pending: bool = False
        self._submit_after_interrupt_ids: tuple[str, ...] = ()
        self._is_submission_deferred = is_submission_deferred
        self._get_input_buffer = get_input_buffer
        self._append_notice = append_notice
        self._invalidate = invalidate
        self._exit_event = asyncio.Event()
        self._exit_expiry_task: asyncio.Task[None] | None = None
        self._interrupt_handler: typing.Callable[
            [], InterruptDisposition
        ] = _ignore_interrupt
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
                (
                    self._is_submission_deferred()
                    or self.pending_steers.uncertain_active
                )
                and self.can_rollback_queued_input
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

    @property
    def can_rollback_queued_input(self) -> bool:
        """返回是否存在可取回的 Tab 输入或被退回即时输入。"""
        return bool(
            self.queued_messages.can_rollback
            or self.rejected_steers.active
            or self.pending_steers.uncertain_active
        )

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

        if submission.history_recorded:
            self.input_model.rollback_submission_history(
                submission.value,
                alternate_text=submission.visible_text,
            )

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
        submission: TuiSubmission
    ) -> None:
        """把用户主动排队的输入保留到后续轮次。"""
        self.queued_messages.append(submission)
        self._invalidate()

    def defer_rejected_steer(self, submission: TuiSubmission) -> None:
        """把未被当前轮次消费的即时输入保留到优先重试队列。"""
        self.pending_steers.remove(submission.client_message_id)
        self.queued_messages.remove(submission.client_message_id)
        self.rejected_steers.remove(submission.client_message_id)
        self.rejected_steers.append(submission)
        self._invalidate()

    def discard_rejected_steer(
        self,
        client_message_id: str
    ) -> TuiSubmission | None:
        """移除已经由当前轮次确认消费的即时输入重试项。"""
        submission = self.rejected_steers.remove(client_message_id)
        if submission is not None:
            self._invalidate()
            return submission
        return None

    def track_pending_steer(self, submission: TuiSubmission) -> None:
        """展示一条等待写入当前轮次的输入。"""
        self.pending_steers.add(submission)
        self._invalidate()

    def mark_pending_steers_interrupt_settling(self) -> None:
        """立即展示等待中断结算的即时输入。"""
        active_ids = self.pending_steers.active_ids
        if active_ids:
            self._submit_after_interrupt_ids = active_ids
        if self.pending_steers.mark_interrupt_settling():
            self._invalidate()

    def resolve_pending_steer(self, client_message_id: str) -> None:
        """停止展示一条已经确认或转入下一轮的输入。"""
        if self.pending_steers.remove(client_message_id) is not None:
            self._invalidate()

    def retain_uncertain_steer(self, submission: TuiSubmission) -> None:
        """保留一条需要用户决定是否重试的即时输入。"""
        self.pending_steers.retain_uncertain(submission)
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
            item = self.rejected_steers.pop_last()
        if item is None:
            item = self.pending_steers.pop_last_uncertain()
        if item is None:
            return False

        self._queued_restore_handler(item)

        if item.history_recorded:
            self.input_model.rollback_submission_history(
                item.value,
                alternate_text=item.visible_text,
            )

        self.queued_submission_text = None

        buffer = self._get_input_buffer()
        buffer.text = item.editable_text
        buffer.cursor_position = len(item.editable_text)

        self.input_model.restore_submission_state(item.paste_store)
        self.input_model.set_shell_mode(item.shell_mode)
        self.input_model.notify_input_layout()
        self._invalidate()

        return True

    def restore_interrupted_submissions(self) -> bool:
        """按中断意图立即提交 steer，或把普通遗留输入恢复到编辑框。"""
        submit_ids = self._submit_after_interrupt_ids
        self._submit_after_interrupt_ids = ()
        if submit_ids:
            immediate: list[TuiSubmission] = []
            for client_message_id in submit_ids:
                submission = self.rejected_steers.remove(client_message_id)
                if submission is None:
                    submission = self.pending_steers.remove(client_message_id)
                if submission is not None:
                    immediate.append(submission)
            if immediate:
                merged = _merge_interrupted_submissions(
                    tuple(immediate),
                    current_text="",
                    current_pastes={},
                    current_shell_mode=False,
                )
                self.rejected_steers.prepend(merged)
                self.clear_exit_confirmation()
                self._invalidate()
                return True

        pending = (
            self.rejected_steers.drain()
            + self.pending_steers.drain()
            + self.queued_messages.drain()
        )
        if not pending:
            return False

        buffer = self._get_input_buffer()
        current_text = buffer.text
        current_pastes = self.input_model.submission_state(current_text)
        merged = _merge_interrupted_submissions(
            pending,
            current_text=current_text,
            current_pastes=current_pastes,
            current_shell_mode=self.input_model.shell_mode,
        )

        for submission in reversed(pending):
            if submission.history_recorded:
                self.input_model.rollback_submission_history(
                    submission.value,
                    alternate_text=submission.visible_text,
                )

        self._queued_restore_handler(merged)
        self.clear_exit_confirmation()
        self.queued_submission_text = None
        buffer.cancel_completion()
        buffer.text = merged.editable_text
        buffer.cursor_position = len(merged.editable_text)
        self.input_model.restore_submission_state(merged.paste_store)
        self.input_model.set_shell_mode(merged.shell_mode)
        self.input_model.notify_input_layout()
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
        handler: typing.Callable[[], InterruptDisposition] | None
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

    def consume_exit_request(self) -> TuiExitReason | None:
        """消费并返回主输入区是否已请求退出。"""
        reason = self.interrupt_state.consume_exit_request()
        if reason is not None:
            self._exit_event.clear()
        return reason

    def discard_input_draft(self) -> bool:
        """清除当前输入草稿并关闭退出确认。"""
        buffer = self._get_input_buffer()
        if not buffer.text and not self.input_model.shell_mode:
            return False

        self.input_model.cancel_history_backtrack()

        buffer.cancel_completion()
        buffer.text = ""
        buffer.cursor_position = 0

        self.input_model.clear_submission_state()
        self.input_model.notify_input_layout()
        self.interrupt_state.disarm_exit()
        self._cancel_exit_expiry()
        self._invalidate()

        return True

    def _request_interrupt_exit(self) -> InterruptDisposition:
        """提交不等待远端结算的中断退出请求。"""
        self.interrupt_state.request_exit()
        self._cancel_exit_expiry()
        self._exit_event.set()
        self._invalidate()
        return InterruptDisposition.EXIT_REQUESTED

    def interrupt_input(self) -> InterruptDisposition:
        """按输入、活动和退出确认的优先级处理中断请求。"""
        if self.discard_input_draft():
            return InterruptDisposition.DRAFT_DISCARDED

        self.input_model.cancel_history_backtrack()
        if self.interrupt_state.exit_armed:
            self._interrupt_handler()
            return self._request_interrupt_exit()

        self.interrupt_state.arm_exit()
        self._schedule_exit_expiry()
        disposition = self._interrupt_handler()

        if disposition is InterruptDisposition.EXIT_REQUESTED:
            return self._request_interrupt_exit()

        self._invalidate()

        if disposition is InterruptDisposition.IGNORED:
            return InterruptDisposition.EXIT_ARMED
        return disposition

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

    def accept_input(self, buffer: Buffer) -> bool:
        """恢复折叠粘贴内容并按当前运行状态提交输入。"""
        if self._input_handoff_pending:
            return True

        self.clear_exit_confirmation()
        self.input_model.cancel_history_backtrack()

        editable_text = buffer.text
        paste_store = self.input_model.submission_state(editable_text)
        shell_mode = self.input_model.shell_mode

        value = self.input_model.restore_submission(buffer.text)
        if not value:
            if shell_mode:
                return False

            if not self.has_pending_attachments:
                return False

        if shell_mode:
            value = f"! {value}" if value else "!"

        history_recorded = self.input_model.record_submission_history(
            editable_text,
            paste_store,
            value=value,
            shell_mode=shell_mode,
        )

        submission = TuiSubmission(
            value=value,
            editable_text=editable_text,
            paste_store=paste_store,
            shell_mode=shell_mode,
            history_recorded=history_recorded,
        )

        submission_deferred = self._is_submission_deferred()

        if submission_deferred:
            policy = (
                None
                if submission.literal_bang_paste
                else stream_command_policy(value)
            )
            if policy is not None:
                self._dispatch_stream_command(submission, policy=policy)
                buffer.text = ""
                buffer.cursor_position = 0
                self.input_model.clear_submission_state()
                self.input_model.notify_input_layout()
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
            self._input_handoff_pending = True
            self.message_queue.put_nowait(submission)

        self.placeholder_text = self.input_model.new_placeholder()

        buffer.cancel_completion()

        buffer.text = submission.visible_text
        buffer.cursor_position = len(buffer.text)

        self.input_model.clear_submission_state()
        if self._input_handoff_pending:
            return True

        if submission_deferred:
            buffer.reset()
            self.input_model.notify_input_layout()
            self._invalidate()
            return True

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
        exit_task = asyncio.create_task(self._exit_event.wait())
        tasks = (submission_task, exit_task)

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

        retried = self.rejected_steers.pop_next()
        queued = self.queued_messages.pop_next() if retried is None else None

        submission = (
            retried
            if retried is not None
            else queued
            if queued is not None
            else await self._read_input_event()
        )

        self._invalidate()

        if submission is _INPUT_CLOSED:
            raise TuiInputClosed

        if self._input_handoff_pending:
            self._input_handoff_pending = False
            self._get_input_buffer().reset()
            self.input_model.notify_input_layout()
            if (
                isinstance(submission, TuiSubmission)
                and submission.history_recorded
                and slash_command_notice_message(submission.value)
            ):
                self.input_model.rollback_submission_history(
                    submission.value,
                    alternate_text=submission.visible_text,
                )

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

        self._input_handoff_pending = False

        self._interrupt_handler = _ignore_interrupt
        self._stream_command_handler = _ignore_stream_command
        self._turn_input_handler = _ignore_turn_input
        self._queued_restore_handler = _ignore_queued_restore
        self._has_pending_attachments = _no_pending_attachments


def _merge_interrupted_submissions(
    submissions: tuple[TuiSubmission, ...],
    *,
    current_text: str,
    current_pastes: dict[str, str],
    current_shell_mode: bool,
) -> TuiSubmission:
    """合并待恢复输入并为折叠粘贴生成无冲突占位符。"""
    chunks = [
        (submission.visible_text, submission.paste_store)
        for submission in submissions
    ]
    if current_text or current_pastes:
        visible_current = (
            f"! {current_text.strip()}"
            if current_shell_mode and current_text.strip()
            else "!"
            if current_shell_mode
            else current_text
        )
        chunks.append((visible_current, current_pastes))

    reserved_placeholders = {
        placeholder
        for text, _paste_store in chunks
        for _start, _end, placeholder in iter_paste_placeholders(text)
    }
    highest_index = max(
        (
            parsed.index
            for placeholder in reserved_placeholders
            if (parsed := parse_paste_placeholder(placeholder)) is not None
        ),
        default=0,
    )
    next_index = highest_index + 1
    merged_texts: list[str] = []
    merged_pastes: dict[str, str] = {}

    for text, paste_store in chunks:
        remapped_text = text
        for _start, _end, placeholder in tuple(iter_paste_placeholders(text)):
            original = paste_store.get(placeholder)
            if original is None:
                continue
            replacement = format_paste_placeholder(original, next_index)
            while replacement in reserved_placeholders or replacement in merged_pastes:
                next_index += 1
                replacement = format_paste_placeholder(original, next_index)
            next_index += 1
            remapped_text = remapped_text.replace(placeholder, replacement, 1)
            merged_pastes[replacement] = original
        if remapped_text:
            merged_texts.append(remapped_text)

    attachments = tuple(
        attachment
        for submission in submissions
        for attachment in submission.attachments
    )
    extras: dict[str, typing.Any] = {}
    for submission in submissions:
        extras.update(submission.extras)

    merged_text = "\n".join(merged_texts)
    single_shell = (
        len(submissions) == 1
        and not current_text
        and not current_pastes
        and submissions[0].shell_mode
    )
    editable_text = submissions[0].editable_text if single_shell else merged_text
    value = submissions[0].value if single_shell else merged_text
    return TuiSubmission(
        value=value,
        editable_text=editable_text,
        paste_store=merged_pastes,
        shell_mode=single_shell,
        attachments=attachments,
        extras=extras,
        payload_bound=any(submission.payload_bound for submission in submissions),
    )


if __name__ == '__main__':
    pass
