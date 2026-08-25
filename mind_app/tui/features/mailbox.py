# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from engine.errors import AppError
from mind_app.frontend import ApplicationView
from mind_app.presentation.mcp_status import render_mcp_status_block
from mind_app.presentation.models import TextSpan
from mind_core.mcp_status import (
    McpStatusDetail,
    McpStatusView
)
from ..core.models import (
    CLOSE_MENU_FOOTER_HINT,
    MailboxEntry,
    MailboxRunRequest,
    MenuActionKind,
    MenuDescriptionLayout,
    MenuOption,
    MenuRequest,
    STANDARD_MENU_FOOTER_HINT
)
from ..core.runtime import TuiRuntime
from ..core.styles import (
    BODY_STYLE,
    BRIGHT_STYLE,
    fragment_block
)

if typing.TYPE_CHECKING:
    from ...controller import Mind
    from ...subscription.runtime import AgentRuntime


@dataclass(frozen=True, slots=True)
class PreparedMailboxRun(object):
    """保存已经通过主循环执行校验的收件箱请求。"""
    listener: "AgentRuntime"
    message_id: str
    prompt: str


_DETAIL_ACTION = "detail"
_RUN_ACTION    = "run"
_DELETE_ACTION = "delete"


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

    @staticmethod
    def _mailbox_failure_panel(message_id: str, error: BaseException) -> MenuRequest:
        """生成收件箱操作失败的只读子面板。"""
        message = str(error).strip() or type(error).__name__
        return MenuRequest(
            title="Mailbox operation",
            view_id=f"mailbox:failure:{message_id}",
            body=(f"Failed: {message}",),
            help_text="",
            footer_hint=CLOSE_MENU_FOOTER_HINT,
        )

    def _select_auto_run(self, value: bool) -> None:
        """处理摘要菜单中的自动运行开关。"""
        self.runtime.emit_menu_action(
            lambda: self._apply_auto_run(value),
            name="tui mailbox menu action",
            kind=MenuActionKind.DOMAIN,
        )

    def _apply_auto_run(self, value: bool) -> None:
        """应用摘要菜单中的自动运行开关。"""
        self.set_auto_run(bool(value))
        render_mailbox_auto_status(self.controller, self.auto_run)
        self.runtime.finish_menu(None)

    def _push_message_menu(self, message_id: str) -> None:
        """从摘要菜单压入单条消息操作菜单。"""
        self.runtime.emit_menu_action(
            lambda: self._push_message_menu_now(message_id),
            name="tui mailbox navigation",
            kind=MenuActionKind.NAVIGATION,
        )

    def _push_message_menu_now(self, message_id: str) -> None:
        """在菜单 action 队列中压入单条消息操作菜单。"""
        entry = self._entry(message_id)
        if entry is None:
            self.runtime.push_menu(self._mailbox_failure_panel(
                message_id,
                RuntimeError("message is no longer available"),
            ))
            return None
        self.runtime.push_menu(self._message_menu(entry))

    def _message_menu(
        self,
        entry: MailboxEntry
    ) -> MenuRequest:
        """生成带有异步操作回调的单条消息菜单。"""

        return MenuRequest(
            title="Mailbox Message",
            view_id=f"mailbox:message:{entry.key}",
            status=entry.title,
            help_text="",
            footer_hint=STANDARD_MENU_FOOTER_HINT,
            description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
            options=(
                MenuOption(
                    _RUN_ACTION,
                    "Run",
                    "Execute this message now.",
                    on_select=lambda: self._queue_message_action(
                        entry.key,
                        _RUN_ACTION,
                    ),
                ),
                MenuOption(
                    _DELETE_ACTION,
                    "Delete",
                    "Cancel this task and remove it from the mailbox.",
                    on_select=lambda: self._queue_message_action(
                        entry.key,
                        _DELETE_ACTION,
                    ),
                ),
                MenuOption(
                    _DETAIL_ACTION,
                    "Detail",
                    "Open the full message.",
                    on_select=lambda: self._queue_message_action(
                        entry.key,
                        _DETAIL_ACTION,
                    ),
                    dismiss_on_select=False,
                ),
            ),
        )

    def _queue_message_action(self, message_id: str, action: str) -> None:
        """把单条消息操作排入菜单事件队列。"""

        self.runtime.emit_menu_action(
            lambda: self._start_message_action(message_id, action),
            name="tui mailbox menu action",
            kind=MenuActionKind.DOMAIN,
        )

    def _start_message_action(self, message_id: str, action: str) -> None:
        """启动由界面生命周期管理的消息后台操作。"""

        self.runtime.start_background_task(
            self._run_message_action(message_id, action),
            name="tui mailbox menu action",
        )

    def _summary_menu(
        self,
        *,
        on_auto: typing.Callable[[bool], None] | None = None,
        on_message: typing.Callable[[str], None] | None = None,
    ) -> MenuRequest:
        """生成当前收件箱摘要菜单。"""
        entries = self.runtime.mailbox_entries()

        auto_options = (
            MenuOption(
                ("auto", True),
                "Auto-run: on",
                "Run pending and newly received messages in order.",
                on_select=(
                    lambda: on_auto(True)
                    if on_auto is not None
                    else None
                ),
                dismiss_on_select=on_auto is None,
            ),
            MenuOption(
                ("auto", False),
                "Auto-run: off",
                "Keep messages pending until run manually.",
                on_select=(
                    lambda: on_auto(False)
                    if on_auto is not None
                    else None
                ),
                dismiss_on_select=on_auto is None,
            ),
        )
        auto_selected = 0 if self.auto_run else 1
        options = (
            *auto_options,
            *(
                MenuOption(
                    ("message", entry.key),
                    entry.title,
                    entry.detail,
                    on_select=(
                        lambda selected=entry.key: on_message(selected)
                        if on_message is not None
                        else None
                    ),
                    dismiss_on_select=on_message is None,
                )
                for entry in entries
            ),
        )

        return MenuRequest(
            title="Mailbox",
            view_id="mailbox:summary",
            status="View and process remote request messages.",
            help_text="",
            footer_hint=STANDARD_MENU_FOOTER_HINT,
            description_layout=MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW,
            options=options,
            selected=len(auto_options) if entries else auto_selected,
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

    def _receipt_disposition(self) -> typing.Literal["queued", "auto_run"]:
        """把会话级自动运行策略映射为收件回执意图。"""
        return "auto_run" if self.auto_run else "queued"

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

    def set_auto_run(self, enabled: bool) -> None:
        """切换当前 TUI 会话的自动运行策略。"""
        self.auto_run = bool(enabled)
        self._refresh()

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

    def prepare_run(
        self,
        request: MailboxRunRequest
    ) -> PreparedMailboxRun | None:
        """校验一条主循环执行请求，并返回稳定执行参数。"""
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

        message = item.request.payload.get("message")

        return PreparedMailboxRun(
            listener=listener,
            message_id=request.message_id,
            prompt=message if isinstance(message, str) else "",
        )

    async def open(self) -> None:
        """打开消息摘要，并按选择进入单条消息操作。"""
        self.bind_listener()
        await self.runtime.select_menu(self._summary_menu(
            on_auto=self._select_auto_run,
            on_message=self._push_message_menu,
        ))

    async def _run_message_action(
        self,
        message_id: str,
        action: str
    ) -> None:
        """执行消息操作并刷新仍在栈中的摘要菜单。"""
        session_id = self.runtime.active_menu_session_id()
        if session_id is None:
            return None

        if action == _DETAIL_ACTION:
            try:
                await self.runtime.view_mailbox_entry(
                    message_id,
                    allow_menu=True,
                )
            except Exception as error:
                if self.runtime.menu_session_is_active(session_id):
                    self.runtime.push_menu(
                        self._mailbox_failure_panel(message_id, error)
                    )
                return None
            if self.runtime.menu_session_is_active(session_id):
                entry = self._entry(message_id)
                if entry is not None:
                    self.runtime.replace_present_menu_if_id(
                        "mailbox:summary",
                        self._summary_menu(
                            on_auto=self._select_auto_run,
                            on_message=self._push_message_menu,
                        ),
                        session_id=session_id,
                    )
            return None

        try:
            if action == _RUN_ACTION:
                self._enqueue_run(message_id, automatic=False)
            elif action == _DELETE_ACTION:
                listener = self._listener
                if listener is None:
                    raise RuntimeError("listener is not running")
                await listener.discard(message_id)
            else:
                return None
        except (KeyError, RuntimeError, ValueError) as error:
            if self.runtime.menu_session_is_active(session_id):
                self.runtime.push_menu(
                    self._mailbox_failure_panel(message_id, error)
                )
            return None

        if not self.runtime.menu_session_is_active(session_id):
            return None
        if action == _DELETE_ACTION:
            render_mailbox_deleted(self.controller)
        self.runtime.finish_menu(None)


def render_mailbox_auto_status(controller: "Mind", enabled: bool) -> None:
    """展示自动运行策略切换结果。"""
    state = "enabled" if enabled else "disabled"
    controller.frontend.application.emit(ApplicationView(
        type="tui.mailbox.status",
        renderable=fragment_block(
            TextSpan("• ", BODY_STYLE),
            TextSpan(f"Mailbox auto-run {state}", BRIGHT_STYLE),
        ),
    ))
    controller.frontend.application.emit(ApplicationView(type="tui.gap"))


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
        details=(McpStatusDetail(f"  └ {detail}", "failed"),),
    )


def _present_status(
    controller: "Mind",
    summary: str,
    *,
    level: typing.Literal["ready", "warning", "failed"] = "ready",
    details: tuple[McpStatusDetail, ...] = ()
) -> None:
    """提交一项收件箱稳定状态。"""
    block = render_mcp_status_block(
        McpStatusView(
            summary=summary,
            level=level,
            done=True,
            details=details,
        ),
        terminal_width=getattr(
            getattr(controller.frontend.application, "viewport", None),
            "width",
            None,
        ),
    )
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
