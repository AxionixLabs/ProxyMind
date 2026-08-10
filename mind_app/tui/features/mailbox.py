# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from engine.errors import AppError
from mind_app.frontend import ApplicationView
from mind_app.presentation.mcp_status import render_mcp_status_block
from mind_core.mcp_status import (
    McpStatusDetail,
    McpStatusView
)
from ..core.mailbox import format_mailbox_count
from ..core.models import (
    MailboxEntry,
    MailboxRunRequest,
    MenuOption,
    MenuRequest
)
from ..core.runtime import TuiRuntime

if typing.TYPE_CHECKING:
    from ...controller import Mind
    from ...subscription.runtime import AgentRuntime


class TuiMailboxFeature(object):
    """协调进程内收件箱菜单、自动消费和主循环执行请求。"""

    def __init__(self, runtime: TuiRuntime, controller: "Mind") -> None:
        """保存会话级收件箱依赖和非持久化自动运行状态。"""
        self.runtime    = runtime
        self.controller = controller
        self.auto_run   = False

        self._listener: AgentRuntime | None = None

        self._automatic_message_id: str    = ""
        self._manual_message_ids: set[str] = set()

    def bind_listener(self) -> None:
        """绑定当前监听器，并将内存消息同步到 TUI 快照。"""
        listener = getattr(self.controller, "subscription_runtime", None)
        if listener is self._listener:
            self._refresh()
            return None

        previous = self._listener
        self._listener = listener
        if previous is not None:
            previous.bind_inbox_changed(None)
            previous.bind_receipt_disposition(None)

        if listener is None:
            self.runtime.set_mailbox_entries((), listener_active=False)
            return None

        listener.bind_receipt_disposition(self._receipt_disposition)
        listener.bind_inbox_changed(self._refresh)

    async def open(self) -> None:
        """打开消息摘要，并按选择进入单条消息操作。"""
        self.bind_listener()
        while True:
            selected = await self.runtime.select_menu(self._summary_menu())
            if selected is None:
                return None

            kind, value = selected
            if kind == "auto":
                self.set_auto_run(bool(value))
                render_mailbox_auto_status(self.controller, self.auto_run)
                return None

            if kind == "message":
                completed = await self._open_message(str(value))
                if completed:
                    return None

    def set_auto_run(self, enabled: bool) -> None:
        """切换当前 TUI 会话的自动运行策略。"""
        self.auto_run = bool(enabled)
        self._refresh()

    def _receipt_disposition(self) -> typing.Literal["queued", "auto_run"]:
        """把会话级自动运行策略映射为收件回执意图。"""
        return "auto_run" if self.auto_run else "queued"

    def prepare_run(
        self,
        request: MailboxRunRequest,
    ) -> "AgentRuntime | None":
        """校验一条主循环执行请求，并返回对应监听器。"""
        if request.automatic:
            if not self._automatic_message_id:
                self._automatic_message_id = request.message_id
        else:
            self._manual_message_ids.add(request.message_id)
        self.bind_listener()
        listener = self._listener

        if request.automatic and not self.auto_run:
            return None
        if listener is None:
            if not request.automatic:
                render_mailbox_failure(
                    self.controller,
                    "Mailbox run failed",
                    RuntimeError("listener is not running"),
                )
            return None

        item = listener.inbox.find(request.message_id)
        if item is None or item.status != "pending":
            if not request.automatic:
                render_mailbox_failure(
                    self.controller,
                    "Mailbox run failed",
                    RuntimeError("message is no longer available"),
                )
            return None

        if not listener.is_ready():
            if not request.automatic:
                render_mailbox_failure(
                    self.controller,
                    "Mailbox run failed",
                    RuntimeError("listener is not ready"),
                )
            return None

        return listener

    def finish_run(self, request: MailboxRunRequest) -> None:
        """释放自动运行占位并继续调度下一条待处理消息。"""
        if (
            request.automatic
            and self._automatic_message_id == request.message_id
        ):
            self._automatic_message_id = ""
        if not request.automatic:
            self._manual_message_ids.discard(request.message_id)
        self._refresh()

    async def _open_message(self, message_id: str) -> bool:
        """打开单条消息操作菜单，返回是否已经完成终态操作。"""
        while True:
            entry = self._entry(message_id)
            if entry is None:
                render_mailbox_failure(
                    self.controller,
                    "Mailbox unavailable",
                    RuntimeError("message is no longer available"),
                )
                return True

            action = await self.runtime.select_menu(MenuRequest(
                title="Mailbox Message",
                status=entry.title,
                options=(
                    MenuOption("run", "Run", "Execute this message now."),
                    MenuOption(
                        "delete",
                        "Delete",
                        "Remove it from this process without notifying the server.",
                    ),
                    MenuOption("detail", "Detail", "Open the full message."),
                ),
                help_text="Up/Down select · Enter apply · Esc/q back",
            ))
            if action is None:
                return False
            if action == "detail":
                await self.runtime.view_mailbox_entry(message_id)
                continue
            if action == "run":
                self._enqueue_run(
                    message_id,
                    automatic=False,
                )
                return True
            if action == "delete":
                listener = self._listener
                try:
                    if listener is None:
                        raise RuntimeError("listener is not running")
                    listener.discard(message_id)
                except (KeyError, RuntimeError, ValueError) as error:
                    render_mailbox_failure(
                        self.controller,
                        "Mailbox delete failed",
                        error,
                    )
                else:
                    render_mailbox_deleted(self.controller)
                return True

    def _summary_menu(self) -> MenuRequest:
        """生成当前收件箱摘要菜单。"""
        entries = self.runtime.mailbox_entries()

        listener = self._listener
        if listener is not None and listener.is_ready():
            listener_state = "listening"
        elif listener is not None and listener.is_running():
            listener_state = "connecting"
        else:
            listener_state = "stopped"

        auto_state = "on" if self.auto_run else "off"

        options = (
            MenuOption(
                ("auto", not self.auto_run),
                f"Auto-run: {auto_state}",
                "Run pending and newly received messages in order.",
            ),
            *(
                MenuOption(
                    ("message", entry.key),
                    entry.title,
                    entry.detail,
                )
                for entry in entries
            ),
        )

        return MenuRequest(
            title="Mailbox",
            status=(
                f"{format_mailbox_count(len(entries))} pending "
                f"· auto={auto_state} · {listener_state}"
            ),
            options=options,
            selected=1 if entries else 0,
        )

    def _entry(self, message_id: str) -> MailboxEntry | None:
        """从当前已过滤快照中查找一条消息。"""
        return next(
            (
                entry
                for entry in self.runtime.mailbox_entries()
                if entry.key == message_id
            ),
            None,
        )

    def _refresh(self) -> None:
        """刷新静态快照，并在安全条件下排入一条自动任务。"""
        listener = self._listener
        if listener is None:
            self.runtime.set_mailbox_entries((), listener_active=False)
            return None

        self.runtime.set_mailbox_entries(
            _mailbox_entries(listener),
            listener_active=listener.is_running(),
        )
        self._schedule_auto()

    def _schedule_auto(self) -> None:
        """至多向 TUI 主循环投递一条自动执行请求。"""
        listener = self._listener
        if (
            not self.auto_run
            or self._automatic_message_id
            or self._manual_message_ids
            or listener is None
            or not listener.is_ready()
        ):
            return None

        item = listener.inbox.next_pending()
        if item is None:
            return None

        message_id = item.request.message_id
        self._enqueue_run(message_id, automatic=True)

    def _enqueue_run(self, message_id: str, *, automatic: bool) -> bool:
        """记录并投递一条尚未排队的消息执行请求。"""
        if (
            message_id == self._automatic_message_id
            or message_id in self._manual_message_ids
        ):
            return False

        if automatic:
            self._automatic_message_id = message_id
        else:
            self._manual_message_ids.add(message_id)
        try:
            self.runtime.enqueue_mailbox_run(
                message_id,
                automatic=automatic,
            )
        except BaseException:
            if automatic and self._automatic_message_id == message_id:
                self._automatic_message_id = ""
            if not automatic:
                self._manual_message_ids.discard(message_id)
            raise
        return True


def render_mailbox_auto_status(controller: "Mind", enabled: bool) -> None:
    """展示自动运行策略切换结果。"""
    state = "enabled" if enabled else "disabled"
    _present_status(controller, f"Mailbox auto-run {state}")


def render_mailbox_deleted(controller: "Mind") -> None:
    """展示本地消息删除结果。"""
    _present_status(controller, "Mailbox message deleted")


def render_mailbox_failure(
    controller: "Mind",
    summary: str,
    error: BaseException
) -> None:
    """展示收件箱操作失败结果。"""
    detail = _error_detail(error)

    _present_status(
        controller,
        summary,
        level="failed",
        details=(McpStatusDetail(f"└ {detail}", "failed"),),
    )


def _present_status(
    controller: "Mind",
    summary: str,
    *,
    level: typing.Literal["ready", "warning", "failed"] = "ready",
    details: tuple[McpStatusDetail, ...] = ()
) -> None:
    """提交一项收件箱稳定状态。"""
    block = render_mcp_status_block(McpStatusView(
        summary=summary,
        level=level,
        done=True,
        details=details,
    ))
    controller.frontend.application.emit(ApplicationView(
        type="tui.mailbox.status",
        renderable=block,
    ))
    controller.frontend.application.emit(ApplicationView(type="tui.gap"))


def _error_detail(error: BaseException) -> str:
    """返回收件箱操作失败时使用的简短详情。"""
    if isinstance(error, AppError):
        return str(error.message)

    message = str(error).strip()

    return (
        f"{type(error).__name__}: {message}"
        if message
        else type(error).__name__
    )


def _mailbox_entries(
    listener: "AgentRuntime"
) -> tuple[MailboxEntry, ...]:
    """把订阅请求转换为不携带传输对象的只读展示快照。"""
    entries: list[MailboxEntry] = []
    for item in listener.inbox.pending_items():
        request = item.request
        payload = request.payload

        message_raw = payload.get("message")
        message     = message_raw if isinstance(message_raw, str) else ""

        intent_raw  = payload.get("intent")
        intent      = intent_raw if isinstance(intent_raw, dict) else {}
        summary_raw = intent.get("summary")
        summary     = summary_raw.strip() if isinstance(summary_raw, str) else ""

        first_line = next(
            (line.strip() for line in message.splitlines() if line.strip()),
            "",
        )
        title = summary or first_line or request.call_id or "Remote request"

        entries.append(MailboxEntry(
            key=request.message_id,
            title=title,
            message=message or "(invalid message payload)",
            detail=(
                f"call {request.call_id} · conversation {request.cid}/{request.sid}"
            ),
        ))

    return tuple(entries)


if __name__ == '__main__':
    pass
