# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from agent.application.config.session_identity import derive_local_session_id
from agent.application.turns.durable_queue import (
    DurableQueueEntry,
    DurableQueueSessionSnapshot,
)
from agent.ports import ProtocolCommandError
from agent.ports.presentation import TextSpan
from agent.protocol import (
    LocalDurableQueueSnapshot,
    SubmitTurnCommand,
)
from agent.protocol.json_value import ThawedJsonValue
from infrastructure.services.turn_environment import (
    capture_active_turn_environment,
)
from protocol.schema.identifiers import (
    new_request_id,
    new_submission_id,
    short_uid,
)
from ..core.models import FragmentBlock
from ..core.runtime import TuiRuntime
from ..core.styles import (
    ACCENT_STYLE,
    BODY_STYLE,
    BRIGHT_STYLE,
    COMMAND_STYLE,
    FAILURE_STYLE,
    MUTED_STYLE,
    fragment_block,
)

if typing.TYPE_CHECKING:
    from ..application import TuiApplicationHost
    from ..session.state import TuiSessionState

QueueCommandName: typing.TypeAlias = typing.Literal[
    "list",
    "add",
    "retry",
    "delete",
    "move",
    "start",
]


@dataclass(frozen=True, slots=True)
class DurableQueueCommand:
    """保存一项经过语法校验的 TUI Queue 命令。"""

    name: QueueCommandName
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DurableQueueDispatchResult:
    """返回命令是否产生一个必须立即观察的 Queue Turn。"""

    started: LocalDurableQueueSnapshot | None = None


def parse_durable_queue_command(value: str) -> DurableQueueCommand:
    """解析显式 Queue 命令并保留 add 正文的原始大小写。"""
    parts = str(value or "").strip().split(maxsplit=2)
    if not parts or parts[0].casefold() != "/queue":
        raise ValueError("queue command must start with /queue")
    if len(parts) == 1:
        return DurableQueueCommand("list")

    action = parts[1].casefold()
    payload = parts[2].strip() if len(parts) == 3 else ""
    if action == "list" and not payload:
        return DurableQueueCommand("list")
    if action == "add" and payload:
        return DurableQueueCommand("add", (payload,))
    if action == "start":
        return DurableQueueCommand(
            "start",
            (payload,) if payload else (),
        )
    if action in {"retry", "delete"} and payload and " " not in payload:
        return DurableQueueCommand(action, (payload,))
    if action == "move" and payload:
        move_parts = payload.split()
        if len(move_parts) == 2 and move_parts[1].isdigit():
            return DurableQueueCommand("move", tuple(move_parts))
    raise ValueError(
        "Usage: /queue [list|add <message>|retry <id>|delete <id>|"
        "move <id> <position>|start [id]]"
    )


class TuiDurableQueueFeature:
    """协调显式持久 Queue 的命令、输入所有权和 TUI 投影。"""

    def __init__(
        self,
        host: "TuiApplicationHost",
        runtime: TuiRuntime,
        state: "TuiSessionState",
    ) -> None:
        """绑定 Queue application 与当前 TUI 会话状态。"""
        self._host = host
        self._runtime = runtime
        self._state = state

    async def dispatch(self, value: str) -> DurableQueueDispatchResult:
        """执行一项 Queue 命令，并把可预期失败留在交互边界。"""
        try:
            command = parse_durable_queue_command(value)
            if command.name == "list":
                await self._show()
                return DurableQueueDispatchResult()
            if command.name == "add":
                await self._add(command.arguments[0])
                return DurableQueueDispatchResult()
            if command.name == "retry":
                await self._retry(command.arguments[0])
                return DurableQueueDispatchResult()
            if command.name == "delete":
                await self._delete(command.arguments[0])
                return DurableQueueDispatchResult()
            if command.name == "move":
                await self._move(
                    command.arguments[0],
                    int(command.arguments[1]),
                )
                return DurableQueueDispatchResult()
            return DurableQueueDispatchResult(
                started=await self._start(
                    command.arguments[0] if command.arguments else ""
                )
            )
        except ProtocolCommandError as error:
            detail = str(error).strip() or error.code
            self._present(fragment_block(
                TextSpan("■ Queue failed", FAILURE_STYLE),
                TextSpan(f" · {detail}", BODY_STYLE),
            ))
        except (LookupError, RuntimeError, TypeError, ValueError) as error:
            self._present(fragment_block(
                TextSpan("■ Queue failed", FAILURE_STYLE),
                TextSpan(f" · {error}", BODY_STYLE),
            ))
        return DurableQueueDispatchResult()

    async def dispatch_background(self, value: str) -> None:
        """执行活动 Turn 期间允许的非 start Queue 命令。"""
        result = await self.dispatch(value)
        if result.started is not None:
            raise RuntimeError("queue start cannot run during an active turn")

    async def recover_started(self) -> LocalDurableQueueSnapshot | None:
        """返回冷恢复后唯一尚未完成观察的 Queue Turn。"""
        if not self._host.conversation.session_bound:
            return None
        snapshot = await self._snapshot()
        started = tuple(
            local
            for local in snapshot.local_only
            if local.status == "started"
        )
        if len(started) > 1:
            raise RuntimeError("multiple durable queue turns require observation")
        return started[0] if started else None

    async def _show(self) -> None:
        """展示服务端权威顺序和本地不确定恢复项。"""
        snapshot = await self._snapshot()
        spans: list[TextSpan] = [
            TextSpan("/queue", COMMAND_STYLE),
            TextSpan("\n\nQueue", BRIGHT_STYLE),
            TextSpan(
                f" · {len(snapshot.entries)} pending"
                f" · version {snapshot.queue_version}",
                MUTED_STYLE,
            ),
        ]
        if not snapshot.entries and not snapshot.local_only:
            spans.append(TextSpan("\n\n  Queue is empty.", MUTED_STYLE))
        for entry in snapshot.entries:
            spans.extend(_entry_spans(entry))
        for local in snapshot.local_only:
            spans.extend(_local_only_spans(local))
        self._present(fragment_block(*spans))

    async def _add(self, message: str) -> None:
        """转移当前附件与扩展输入所有权，并冻结一次 Queue add。"""
        cid, sid = self._coordinates()
        submission_id = new_submission_id()
        request_id = new_request_id("queue_add")
        client_message_id = f"message_{submission_id}"
        turn_id = short_uid(12)
        attachments = tuple(self._host.attach.consume_pending_attachments())
        extras = self._state.consume_pending_prompt_extras()
        command = SubmitTurnCommand.create(
            session_id=derive_local_session_id(
                "tui",
                {"cid": cid, "sid": sid},
            ),
            message=message,
            attachments=attachments,
            environment_snapshot=capture_active_turn_environment(self._host),
            pref_config=self._state.pref_config,
            extras=extras,
            trace_context={
                "remote_turn": {
                    "cid": cid,
                    "sid": sid,
                    "turn_id": turn_id,
                },
            },
        )
        try:
            result = await self._host.enqueue_durable_turn(
                command,
                permissions=self._state.permissions,
                submission_id=submission_id,
                client_message_id=client_message_id,
                request_id=request_id,
            )
        except BaseException:
            local = await self._host.durable_queue.local_snapshot(submission_id)
            if local is None or local.status == "deleted":
                self._restore_input(attachments, extras)
            raise

        item = result.receipt.item
        self._present(fragment_block(
            TextSpan("• Queued", ACCENT_STYLE),
            TextSpan(f" #{item.position}", BRIGHT_STYLE),
            TextSpan(f" · {_short_id(item.submission_id)}", MUTED_STYLE),
            TextSpan(f"\n  {_preview(item.input.text)}", BODY_STYLE),
        ))

    async def _retry(self, query: str) -> None:
        """使用本地账本中的原 request id 重试结果未知的 add。"""
        snapshot = await self._snapshot()
        candidates = tuple(
            local for local in snapshot.local_only if local.status == "adding"
        )
        local = _resolve_local(candidates, query)
        result = await self._host.durable_queue.retry_add(local.submission_id)
        self._present(fragment_block(
            TextSpan("• Queue confirmed", ACCENT_STYLE),
            TextSpan(
                f" · {_short_id(result.local.submission_id)}",
                MUTED_STYLE,
            ),
        ))

    async def _delete(self, query: str) -> None:
        """删除一个远端权威 Queue item。"""
        snapshot = await self._snapshot()
        entry = _resolve_entry(snapshot.entries, query)
        await self._host.durable_queue.delete(
            cid=snapshot.cid,
            sid=snapshot.sid,
            submission_id=entry.item.submission_id,
        )
        self._present(fragment_block(
            TextSpan("• Queue item deleted", ACCENT_STYLE),
            TextSpan(f" · {_short_id(entry.item.submission_id)}", MUTED_STYLE),
        ))

    async def _move(self, query: str, position: int) -> None:
        """基于单次权威快照提交完整新顺序。"""
        snapshot = await self._snapshot()
        entry = _resolve_entry(snapshot.entries, query)
        if position < 1 or position > len(snapshot.entries):
            raise ValueError("queue position is out of range")
        submission_ids = [item.item.submission_id for item in snapshot.entries]
        submission_ids.remove(entry.item.submission_id)
        submission_ids.insert(position - 1, entry.item.submission_id)
        await self._host.durable_queue.reorder(
            cid=snapshot.cid,
            sid=snapshot.sid,
            submission_ids=submission_ids,
        )
        self._present(fragment_block(
            TextSpan("• Queue reordered", ACCENT_STYLE),
            TextSpan(f" · {_short_id(entry.item.submission_id)}", MUTED_STYLE),
            TextSpan(f" → #{position}", BODY_STYLE),
        ))

    async def _start(self, query: str) -> LocalDurableQueueSnapshot:
        """显式启动一个 Queue item 并返回 attach 所需冻结快照。"""
        snapshot = await self._snapshot()
        local = _start_candidate(snapshot, query)
        result = await self._host.durable_queue.start(
            cid=snapshot.cid,
            sid=snapshot.sid,
            submission_id=local.submission_id,
        )
        return result.local

    async def _snapshot(self) -> DurableQueueSessionSnapshot:
        """读取当前 Session 的一次有界 Queue 恢复快照。"""
        cid, sid = self._coordinates()
        return await self._host.durable_queue.reconcile(cid=cid, sid=sid)

    def _coordinates(self) -> tuple[str, str]:
        """返回已经在服务端建立的当前 Session 坐标。"""
        if not self._host.conversation.session_bound:
            raise RuntimeError("start a conversation before using durable queue")
        snapshot = self._host.conversation.snapshot()
        return snapshot["cid"], snapshot["sid"]

    def _restore_input(
        self,
        attachments: tuple[dict[str, ThawedJsonValue], ...],
        extras: dict[str, ThawedJsonValue],
    ) -> None:
        """在 Queue 未取得本地所有权时恢复结构化草稿。"""
        current_attachments = tuple(
            self._host.attach.pending_attachments_snapshot()
        )
        self._host.attach.replace_pending_attachments(
            attachments + current_attachments
        )
        current_extras = self._state.consume_pending_prompt_extras()
        restored_extras = dict(extras)
        restored_extras.update(current_extras)
        if restored_extras:
            self._state.replace_pending_prompt_extras(restored_extras)

    def _present(self, block: FragmentBlock) -> None:
        """在稳定帧边界展示 Queue 结果。"""
        self._runtime.queue_background_block(block)


def _entry_spans(entry: DurableQueueEntry) -> tuple[TextSpan, ...]:
    """构造一个远端 Queue item 的紧凑展示行。"""
    item = entry.item
    availability = "ready" if entry.executable else "snapshot unavailable"
    return (
        TextSpan(f"\n\n  {item.position}. ", BODY_STYLE),
        TextSpan(_preview(item.input.text), BODY_STYLE),
        TextSpan(
            f"\n     {_short_id(item.submission_id)} · {availability}",
            MUTED_STYLE,
        ),
    )


def _local_only_spans(local: LocalDurableQueueSnapshot) -> tuple[TextSpan, ...]:
    """构造响应未知或待恢复观察项的展示行。"""
    labels = {
        "adding": "add confirmation unknown",
        "starting": "start confirmation unknown",
        "started": "observation pending",
    }
    return (
        TextSpan("\n\n  • ", BODY_STYLE),
        TextSpan(_preview(local.command.message), BODY_STYLE),
        TextSpan(
            f"\n     {_short_id(local.submission_id)} · "
            f"{labels.get(local.status, local.status)}",
            MUTED_STYLE,
        ),
    )


def _start_candidate(
    snapshot: DurableQueueSessionSnapshot,
    query: str,
) -> LocalDurableQueueSnapshot:
    """解析显式 start 目标，并优先恢复已经启动的本地 Turn。"""
    locals_by_id = {
        local.submission_id: local
        for local in snapshot.local_only
        if local.status in {"starting", "started"}
    }
    for entry in snapshot.entries:
        if entry.local is not None:
            locals_by_id[entry.item.submission_id] = entry.local
    candidates = tuple(locals_by_id.values())
    if query:
        return _resolve_local(candidates, query)
    for status in ("started", "starting"):
        for local in candidates:
            if local.status == status:
                return local
    if snapshot.entries:
        local = snapshot.entries[0].local
        if local is None:
            raise RuntimeError(
                "queue head has no local execution snapshot on this client"
            )
        return local
    raise LookupError("durable queue is empty")


def _resolve_entry(
    entries: tuple[DurableQueueEntry, ...],
    query: str,
) -> DurableQueueEntry:
    """按完整 ID 或唯一前缀解析一个远端 Queue item。"""
    matches = tuple(
        entry for entry in entries if entry.item.submission_id.startswith(query)
    )
    if len(matches) != 1:
        raise LookupError("queue id is missing or ambiguous")
    return matches[0]


def _resolve_local(
    locals_: tuple[LocalDurableQueueSnapshot, ...],
    query: str,
) -> LocalDurableQueueSnapshot:
    """按完整 ID 或唯一前缀解析一个本地执行快照。"""
    matches = tuple(
        local for local in locals_ if local.submission_id.startswith(query)
    )
    if len(matches) != 1:
        raise LookupError("queue id is missing or ambiguous")
    return matches[0]


def _short_id(value: str) -> str:
    """返回足以用于当前列表前缀选择的紧凑标识。"""
    return value[:18]


def _preview(value: str, *, limit: int = 120) -> str:
    """生成不会破坏 Queue 列表布局的单行输入摘要。"""
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[:limit - 3]}..."


if __name__ == '__main__':
    pass
