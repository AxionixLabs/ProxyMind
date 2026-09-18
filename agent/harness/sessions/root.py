# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import contextlib
import typing
from dataclasses import asdict
from collections.abc import (
    Awaitable,
    Callable,
    Iterable,
)
from pathlib import Path

from agent.application.config.session_identity import derive_local_session_id
from agent.application.agents.views import AgentSnapshot
from agent.application.turns.compact_result import CompactEvent
from agent.application.turns.context_usage import ContextUsageProjection
from agent.application.hooks.context import HookExecutionContext
from agent.domain.hooks import SessionEndReason
from agent.domain.policies import PermissionSettings
from agent.domain.transcripts import (
    TranscriptEntry,
    TranscriptReplay,
)
from agent.harness.hooks.session_lifecycle import SessionLifecycleGateway
from agent.harness.sessions.conversation import (
    ConversationState,
    ConversationTurn,
)
from agent.ports import (
    ApprovalLedger,
    ConversationHistoryPort,
    HookScopeProviderPort,
    PermissionGrantReader,
    TranscriptFactory,
)
from agent.protocol import AssistantReplySnapshot
from agent.protocol.context_usage import ContextUsageRecord
from agent.ports.conversation import (
    ContextUsageFeed,
    ContextUsageRecovery,
    ContextUsageRecoveryError,
)
from agent.ports.compaction import (
    CompactionRecovery,
    CompactionRecoveryError,
)
from agent.ports.session_deletion import (
    LocalDeletionPlan,
    LocalDeletionTarget,
    RemoteDeletionReceipt,
    RemoteDeletionRequest,
    RemoteDeletionTarget,
    SessionDeletionConflict,
    SessionDeletionRemote,
    SessionDeletionRemoteError,
    SessionDeletionResult,
    SessionDeletionStore,
)
from agent.ports.transcript import TranscriptSessionPort
from agent.ports.workspace import WorkspaceChangePort
from observability import (
    observe,
    observe_exception,
)
from protocol.schema.identifiers import valid_session_ids

WorkspaceProvider: typing.TypeAlias = Callable[[], str | Path]
PermissionProvider: typing.TypeAlias = Callable[[], PermissionSettings]
PreferenceConfigProvider: typing.TypeAlias = Callable[[], dict[str, typing.Any]]
FreshPreferenceProvider: typing.TypeAlias = Callable[
    [float],
    Awaitable[dict[str, typing.Any]],
]
TranscriptPathProvider: typing.TypeAlias = Callable[[str], str]
SubagentShutdown: typing.TypeAlias = Callable[
    [str],
    Awaitable[tuple[AgentSnapshot, ...]],
]
SessionCleanup: typing.TypeAlias = Callable[[str], Awaitable[None]]
SessionRuntimeClose: typing.TypeAlias = Callable[[str], Awaitable[None]]
CommandHookCleanup: typing.TypeAlias = Callable[[str], None]
EventSessionClose: typing.TypeAlias = Callable[[str, str], Awaitable[None]]
CleanupValue = typing.TypeVar("CleanupValue")
CleanupWaiter: typing.TypeAlias = Callable[
    [Awaitable[CleanupValue]],
    Awaitable[CleanupValue],
]


def _last_copyable_assistant_reply(
    entries: Iterable[TranscriptEntry],
) -> AssistantReplySnapshot | None:
    """从归并后的记录恢复最近一条可复制 assistant 原文。"""
    for entry in reversed(TranscriptReplay(entries).build()):
        if entry.event != "message.created" or entry.actor != "assistant":
            continue
        if entry.payload.get("phase") == "commentary":
            continue
        content = entry.payload.get("content")
        if isinstance(content, str) and content.strip():
            return AssistantReplySnapshot.from_source(content)
    return None


def _validate_deletion_receipt(
    request: RemoteDeletionRequest,
    receipt: RemoteDeletionReceipt,
) -> None:
    """在生命周期层再次核对适配器返回的完整删除范围。"""
    expected = tuple((item.cid, item.sid) for item in request.targets)
    actual = tuple((item.cid, item.sid) for item in receipt.targets)
    if (
        receipt.request_id != request.request_id
        or (receipt.root.cid, receipt.root.sid) != (request.root.cid, request.root.sid)
        or actual != expected
    ):
        raise SessionDeletionRemoteError(outcome="unknown", code="invalid_receipt")


def _request_from_plan(
    plan: LocalDeletionPlan,
    *,
    root_identity: tuple[str, str] | None = None,
) -> RemoteDeletionRequest:
    """从持久计划恢复原始远端删除范围。"""
    targets = tuple(
        RemoteDeletionTarget(target.cid, target.sid)
        for target in plan.targets
    )
    if not targets:
        raise SessionDeletionConflict("deletion plan has no targets")
    root = next(
        (target for target in targets if root_identity == (target.cid, target.sid)),
        targets[0],
    )
    return RemoteDeletionRequest(
        request_id=plan.request_id,
        root=root,
        descendants=tuple(target for target in targets if target != root),
    )


class RootConversationSession:
    """拥有根会话状态、历史游标、Transcript 和结束事务。"""

    def __init__(
        self,
        history: ConversationHistoryPort,
        *,
        context_usage_recovery: ContextUsageRecovery,
        compaction_recovery: CompactionRecovery,
        workspace: WorkspaceProvider,
        permissions: PermissionProvider,
        preference_config: PreferenceConfigProvider,
        fresh_preferences: FreshPreferenceProvider,
        permission_grants: PermissionGrantReader | None,
        approval_ledger: ApprovalLedger | None,
        output_record_path: str,
        transcript_factory: TranscriptFactory,
        transcript_path_for: TranscriptPathProvider,
        hook_scope_provider: HookScopeProviderPort,
        session_lifecycle: SessionLifecycleGateway,
        subagent_shutdown: SubagentShutdown,
        hook_session_cleanup: SessionCleanup,
        javascript_session_cleanup: SessionCleanup,
        command_hook_cleanup: CommandHookCleanup,
        event_session_close: EventSessionClose,
        await_cleanup: CleanupWaiter,
        session_deletion_store: SessionDeletionStore | None = None,
        session_deletion_remote: SessionDeletionRemote | None = None,
        session_runtime_close: SessionRuntimeClose | None = None,
    ) -> None:
        self._history = history
        self._context_usage_recovery = context_usage_recovery
        self._compaction_recovery = compaction_recovery
        self._compaction_scope: tuple[str, str] | None = None
        self._pending_compactions: dict[str, int] = {}
        self._compaction_recovery_task: asyncio.Task[tuple[CompactEvent, ...]] | None = None
        self._workspace = workspace
        self._permissions = permissions
        self._preference_config = preference_config
        self._fresh_preferences = fresh_preferences
        self._permission_grants = permission_grants
        self._approval_ledger = approval_ledger
        self._output_record_path = str(output_record_path or "")
        self._transcript_factory = transcript_factory
        self._transcript_path_for = transcript_path_for
        self._hook_scope_provider = hook_scope_provider
        self._session_lifecycle = session_lifecycle
        self._subagent_shutdown = subagent_shutdown
        self._hook_session_cleanup = hook_session_cleanup
        self._javascript_session_cleanup = javascript_session_cleanup
        self._command_hook_cleanup = command_hook_cleanup
        self._event_session_close = event_session_close
        self._await_cleanup = await_cleanup
        self._session_deletion_store = session_deletion_store
        self._session_deletion_remote = session_deletion_remote
        self._session_runtime_close = session_runtime_close
        self._session_deletion_lock = asyncio.Lock()
        self._deleted_identity: tuple[str, str] | None = None
        self._state = ConversationState()
        self._lifecycle_id = 0
        self._assistant_reply_snapshot: AssistantReplySnapshot | None = None
        self._context_usage = ContextUsageProjection()

    @property
    def context_usage(self) -> ContextUsageFeed:
        """提供跨 Turn 保留的用量展示订阅。"""
        return self._context_usage

    @property
    def cid(self) -> str | None:
        """返回当前会话的 conversation ID。"""
        return self._state.cid

    @property
    def sid(self) -> str | None:
        """返回当前会话的 session ID。"""
        return self._state.sid

    @property
    def turn_count(self) -> int:
        """返回当前根会话已经开始的轮次数。"""
        return self._state.turn_count

    @property
    def session_bound(self) -> bool:
        """返回当前会话是否已经绑定稳定坐标。"""
        return self._state.session_bound

    @property
    def session_retired(self) -> bool:
        """返回当前会话是否已完成远端删除并退出可恢复生命周期。"""
        return self._deleted_identity == (
            str(self._state.cid or "").strip(),
            str(self._state.sid or "").strip(),
        )

    @property
    def fork_source_available(self) -> bool:
        """返回当前会话是否存在可分支的输入。"""
        return self._state.fork_source_available

    @property
    def workspace_root(self) -> str:
        """返回当前根会话使用的工作区。"""
        return str(self._workspace() or "")

    @property
    def permissions(self) -> PermissionSettings:
        """返回当前根会话的权限设置。"""
        return self._permissions()

    @property
    def permission_grants(self) -> PermissionGrantReader | None:
        """返回当前会话的权限授予读取端口。"""
        return self._permission_grants

    @property
    def output_record_path(self) -> str:
        """返回根轮次输出记录路径。"""
        return self._output_record_path

    @property
    def approval_ledger(self) -> ApprovalLedger | None:
        """返回当前会话共享的审批调用账本。"""
        return self._approval_ledger

    @property
    def transcript_factory(self) -> TranscriptFactory:
        """返回会话 Transcript writer 工厂。"""
        return self._transcript_factory

    @property
    def hook_scope_provider(self) -> HookScopeProviderPort:
        """返回会话生命周期使用的 Hook 作用域提供器。"""
        return self._hook_scope_provider

    @property
    def history_ttl_ms(self) -> int:
        """返回本地历史游标的保留时间。"""
        return self._history.ttl_ms

    @property
    def history_max_items(self) -> int:
        """返回本地历史游标的容量上限。"""
        return self._history.max_items

    @property
    def history(self) -> ConversationHistoryPort:
        """返回当前根会话绑定的本地历史所有者。"""
        return self._history

    @staticmethod
    def _deletion_request(
        request_id: str,
        cid: str,
        sid: str,
        snapshots: tuple[AgentSnapshot, ...],
    ) -> RemoteDeletionRequest:
        """从已关闭子任务快照冻结正式线上删除范围。"""
        descendants = tuple(
            RemoteDeletionTarget(snapshot.thread.cid, snapshot.thread.sid)
            for snapshot in snapshots
            if valid_session_ids(snapshot.thread.cid, snapshot.thread.sid)
            and (snapshot.thread.cid, snapshot.thread.sid) != (cid, sid)
        )
        return RemoteDeletionRequest(
            request_id=request_id,
            root=RemoteDeletionTarget(cid, sid),
            descendants=tuple(dict.fromkeys(descendants)),
        )

    @staticmethod
    def _deletion_plan(
        request: RemoteDeletionRequest,
        snapshots: tuple[AgentSnapshot, ...],
    ) -> LocalDeletionPlan:
        """把远端范围映射成已确认的本地身份集合。"""
        targets: list[LocalDeletionTarget] = []
        for remote_target in request.targets:
            local_ids = {
                derive_local_session_id(source, {
                    "cid": remote_target.cid,
                    "sid": remote_target.sid,
                })
                for source in ("cli", "tui")
            }
            for snapshot in snapshots:
                if (snapshot.thread.cid, snapshot.thread.sid) == (
                    remote_target.cid,
                    remote_target.sid,
                ):
                    local_ids.add(derive_local_session_id(
                        snapshot.thread.source,
                        {"cid": remote_target.cid, "sid": remote_target.sid},
                    ))
            targets.append(LocalDeletionTarget(
                remote_target.cid,
                remote_target.sid,
                tuple(sorted(local_ids)),
            ))
        return LocalDeletionPlan(request.request_id, tuple(targets))

    def _session_hook_context(self, *, cid: str, sid: str) -> HookExecutionContext:
        """构建根会话生命周期事件使用的固定上下文。"""
        pref_config = self._preference_config()
        primary = pref_config.get("primary") if isinstance(pref_config, dict) else None
        model = (
            str(primary.get("model") or "").strip()
            if isinstance(primary, dict)
            else ""
        )
        permissions = self.permissions
        return HookExecutionContext(
            session_id=sid,
            root_session_id=sid,
            conversation_id=cid,
            turn_id="",
            cwd=self.workspace_root,
            model=model,
            source="session",
            sandbox_mode=permissions.sandbox_mode,
            permission_mode=permissions.approval_policy,
            agent_id="root",
            agent_type="root",
            agent_depth=0,
        )

    def _activate_context_usage(self, cid: str, sid: str, *, initial: bool) -> None:
        """切换活动投影；已有会话先进入 pending，再恢复精确缓存。"""
        self._context_usage.activate(cid, sid, initial=initial)
        if not initial:
            record = self._history.load_context_usage(cid, sid)
            if record is not None:
                self._context_usage.apply(record)

    def update_title(
        self,
        cid: str,
        sid: str,
        title: str,
        *,
        source: str = "remote",
    ) -> bool:
        """应用属于当前会话的权威标题更新。"""
        current_cid = str(self._state.cid or "").strip()
        current_sid = str(self._state.sid or "").strip()
        if (
            not self._state.session_bound
            or (cid, sid) != (current_cid, current_sid)
        ):
            observe(
                "conversation.title_update.skipped",
                level="WARNING",
                reason="stale_session",
                cid=cid,
                sid=sid,
                source=source,
            )
            return False

        persisted = self._history.update_title(cid, sid, title)
        observe(
            "conversation.title_updated",
            level="INFO" if persisted else "WARNING",
            cid=cid,
            sid=sid,
            source=source,
            persisted=persisted,
        )
        return True

    def transcript_path_for_session(self, sid: str) -> str:
        """返回指定会话的 Transcript 路径。"""
        return self._transcript_path_for(sid)

    def snapshot(self) -> dict[str, str]:
        """返回当前会话的稳定身份快照。"""
        unbound = self._state.cid is None or self._state.sid is None
        metadata = self._state.snapshot()
        if unbound:
            self._activate_context_usage(metadata["cid"], metadata["sid"], initial=True)
        return metadata

    def record_compaction(self, event: CompactEvent) -> None:
        """保存已观察的手动压缩身份及水位，终态确认后解除待核对状态。"""
        if (event.cid, event.sid) != (self.cid, self.sid) or event.turn_id or event.trigger != "manual":
            return
        if not event.item_id or event.event_seq is None:
            return
        scope = (event.cid, event.sid)
        if self._compaction_scope != scope:
            self._pending_compactions.clear()
            self._compaction_scope = scope
        if event.status == "started":
            self._pending_compactions[event.item_id] = event.event_seq
        elif event.status in {"completed", "failed"}:
            self._pending_compactions.pop(event.item_id, None)

    def record_context_usage(self, record: ContextUsageRecord) -> None:
        """先缓存当前会话的新事实，再提交展示投影。"""
        if self._context_usage.accepts(record):
            self._history.save_context_usage(record)
            self._context_usage.apply(record)

    def context_usage_recovery(self, cid: str, sid: str, *, pending: bool) -> None:
        """沿既有恢复边界抑制中间画面或提交最终快照。"""
        if pending:
            self._context_usage.begin_replay(cid, sid)
        else:
            self._context_usage.finish_replay(cid, sid)

    def discard_context_usage_prefix(self, cid: str, sid: str, event_seq: int) -> None:
        """沿远端裁剪信号作废旧投影及缓存，禁止重启后恢复失效值。"""
        self._context_usage.discard_retained_prefix(cid, sid, event_seq)
        self._history.discard_context_usage_prefix(cid, sid, event_seq)

    def bind_session_runtime_close(self, callback: SessionRuntimeClose) -> None:
        """绑定当前前端 Turn owner 的永久会话封锁回调。"""
        if not callable(callback):
            raise TypeError("session runtime close callback must be callable")
        self._session_runtime_close = callback

    def queue_turn_context(self, contexts: Iterable[str]) -> None:
        """把未完成轮次的上下文排入下一轮。"""
        self._state.queue_turn_context(contexts)

    def remember_assistant_reply(self, text: str) -> None:
        """保存最近一次已完成且可复制的 assistant 原文。"""
        if isinstance(text, str) and text.strip():
            self._assistant_reply_snapshot = AssistantReplySnapshot.from_source(
                text
            )

    def assistant_reply_snapshot(self) -> AssistantReplySnapshot | None:
        """返回最近一次完整模型回复的稳定快照。"""
        return self._assistant_reply_snapshot

    async def _finish_end(
        self,
        *,
        reason: SessionEndReason,
        cid: str,
        sid: str,
        transcript_path: str,
        transcript: TranscriptSessionPort,
        subagent_snapshots: tuple[AgentSnapshot, ...],
    ) -> None:
        """在已收束子任务后写入终态 Hook 并释放会话资源。"""
        def record_session_end() -> None:
            """在结束 Hook 前写入根会话终态。"""
            transcript.open()
            try:
                transcript.append(
                    "session.ended",
                    actor="system",
                    payload={"reason": reason},
                )
            finally:
                transcript.close()

        for snapshot in subagent_snapshots:
            await self._hook_session_cleanup(snapshot.thread.sid)
            with contextlib.suppress(Exception):
                await self._javascript_session_cleanup(snapshot.thread.sid)
        with contextlib.suppress(Exception):
            await self._javascript_session_cleanup(sid)

        self._command_hook_cleanup(sid)
        await self._session_lifecycle.end(
            self._lifecycle_id,
            self._session_hook_context(cid=cid, sid=sid),
            reason=reason,
            transcript_path=transcript_path,
            last_assistant_message=(
                self._assistant_reply_snapshot.content
                if self._assistant_reply_snapshot is not None
                else ""
            ),
            before_dispatch=record_session_end,
        )
        await self._event_session_close(cid, sid)

    async def _restore_manual_compactions(self, cid: str, sid: str) -> None:
        """从持久 started 证据恢复未知 Item，并只回写匹配的远端终态。"""
        if (self.cid, self.sid) != (cid, sid):
            return
        if self._compaction_scope != (cid, sid):
            self._pending_compactions.clear()
            self._compaction_scope = (cid, sid)
        for entry in self._history.read_transcript(sid):
            payload = entry.payload
            if entry.turn_id or (payload.get("cid"), payload.get("sid")) != (cid, sid):
                continue
            if payload.get("trigger") != "manual":
                continue
            item_id, seq = payload.get("item_id"), payload.get("event_seq")
            if not isinstance(item_id, str) or not item_id or isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
                continue
            if entry.event == "context.compaction.started":
                self._pending_compactions[item_id] = seq
            elif entry.event in {"context.compacted", "context.compaction.failed"}:
                self._pending_compactions.pop(item_id, None)
        if not self._pending_compactions:
            return
        task = asyncio.create_task(self._compaction_recovery.load(
            cid, sid, after_seq=min(self._pending_compactions.values()),
        ))
        self._compaction_recovery_task = task
        lifecycle_id = self._lifecycle_id
        try:
            events = await task
        except CompactionRecoveryError:
            observe("compact.recovery.unknown", level="WARNING", cid=cid, sid=sid)
            return
        finally:
            if self._compaction_recovery_task is task:
                self._compaction_recovery_task = None
        if lifecycle_id != self._lifecycle_id or (self.cid, self.sid) != (cid, sid):
            return
        transcript = self._transcript_factory(self._transcript_path_for(sid), session_id=sid)
        transcript.open()
        try:
            for event in events:
                cursor = self._pending_compactions.get(event.item_id)
                if (
                    cursor is None or event.event_seq is None or event.event_seq <= cursor
                    or (event.cid, event.sid) != (cid, sid) or event.turn_id
                    or event.trigger != "manual" or event.status not in {"completed", "failed"}
                ):
                    continue
                transcript.append(
                    "context.compacted" if event.status == "completed" else "context.compaction.failed",
                    actor="system", payload=asdict(event),
                )
                self.record_compaction(event)
        finally:
            transcript.close()

    async def restore_context_usage(self, cid: str, sid: str, *, publish: bool = True) -> None:
        """读取完整权威快照，恢复期间隐藏缓存且不推进事件确认水位。"""
        if (self.cid, self.sid) != (cid, sid):
            return
        self._context_usage.begin_replay(cid, sid)
        try:
            record = await self._context_usage_recovery.load(cid, sid)
        except ContextUsageRecoveryError:
            observe("context_usage.recovery.failed", level="WARNING", cid=cid, sid=sid)
            record = None
        if (self.cid, self.sid) != (cid, sid):
            return
        self._context_usage.restore(cid, sid, record)
        if record is not None:
            self._history.save_context_usage(record)
        if publish:
            self._context_usage.finish_replay(cid, sid)

    async def fresh_pref_config(
        self,
        *,
        ttl_sec: float,
    ) -> dict[str, typing.Any]:
        """返回刷新后的偏好配置快照。"""
        return await self._fresh_preferences(ttl_sec)

    async def await_cleanup(
        self,
        awaitable: Awaitable[CleanupValue],
    ) -> CleanupValue:
        """等待会话清理操作在取消态下收束。"""
        return await self._await_cleanup(awaitable)

    async def begin_turn(
        self,
        cid: str | None = None,
        sid: str | None = None,
        *,
        title: str = "",
        source: str = "begin",
    ) -> ConversationTurn:
        """为新轮次初始化或续用当前会话标识。"""
        previous_identity = (self._state.cid, self._state.sid)
        external_cid = str(cid or "").strip()
        external_sid = str(sid or "").strip()
        if self._deleted_identity is not None and (
            not external_cid
            or (external_cid, external_sid) == self._deleted_identity
        ):
            raise SessionDeletionConflict("deleted session cannot accept new turns")
        if external_cid or external_sid:
            if not valid_session_ids(external_cid, external_sid):
                raise ValueError("valid cid and sid are required")
            if (
                self._state.cid
                and self._state.sid
                and (
                external_cid != self._state.cid
                or external_sid != self._state.sid
            )
            ):
                await self.end(reason="switch")
                self._lifecycle_id += 1
                self._assistant_reply_snapshot = None

        turn = self._state.begin_turn(
            cid=cid,
            sid=sid,
            start_reason=source,
        )
        metadata = turn.metadata()
        self._history.touch(
            metadata,
            workspace=self.workspace_root,
            title=title,
            source=source,
        )
        if previous_identity != (turn.cid, turn.sid):
            self._activate_context_usage(
                turn.cid, turn.sid, initial=turn.session_mode == "create",
            )
            if turn.session_mode != "create":
                await self.restore_context_usage(turn.cid, turn.sid)
                await self._restore_manual_compactions(turn.cid, turn.sid)
        self._context_usage.mark_started()
        observe(
            "conversation.begin",
            cid=metadata.get("cid"),
            sid=metadata.get("sid"),
            source=source,
            session_started=turn.session_started,
            start_reason=turn.start_reason,
        )
        return turn

    async def reset(
        self,
        *,
        reason: str = "manual",
        source: str = "reset",
        title: str = "",
    ) -> dict[str, str]:
        """结束当前生命周期并开始一个新的模型对话。"""
        await self.end(reason="reset")
        metadata = self._state.reset(reason=reason)
        self._activate_context_usage(metadata["cid"], metadata["sid"], initial=True)
        self._lifecycle_id += 1
        self._assistant_reply_snapshot = None
        self._history.touch(
            metadata,
            workspace=self.workspace_root,
            title=title,
            source=source,
        )
        observe(
            "conversation.reset",
            cid=metadata.get("cid"),
            sid=metadata.get("sid"),
            reason=reason,
            source=source,
        )
        return metadata

    async def resume(
        self,
        record: dict[str, typing.Any],
        *,
        source: str = "resume",
        workspace_change: WorkspaceChangePort | None = None,
    ) -> dict[str, str] | None:
        """把当前会话绑定到 history 中选中的坐标。"""
        cid = str(record.get("cid") or "").strip()
        sid = str(record.get("sid") or "").strip()
        if not valid_session_ids(cid, sid):
            observe(
                "history.resume.skipped",
                level="WARNING",
                reason="invalid_cursor",
                cid=cid,
                sid=sid,
            )
            return None
        if self._deleted_identity == (cid, sid):
            raise SessionDeletionConflict("deleted session cannot be resumed")
        metadata = await self.bind(cid, sid, source=source, workspace_change=workspace_change)
        if metadata is not None:
            observe("history.resumed", cid=cid, sid=sid)
        return metadata

    async def bind(
        self,
        cid: str,
        sid: str,
        *,
        source: str = "bind",
        workspace_change: WorkspaceChangePort | None = None,
    ) -> dict[str, str] | None:
        """把当前运行绑定到一组已存在的远端会话标识。"""
        if not valid_session_ids(cid, sid):
            observe(
                "conversation.bind.skipped",
                level="WARNING",
                reason="invalid_cursor",
                cid=cid,
                sid=sid,
                source=source,
            )
            return None
        if self._deleted_identity == (cid, sid):
            raise SessionDeletionConflict("deleted session cannot be bound")
        if self._state.cid == cid and self._state.sid == sid and workspace_change is None:
            if not self._state.fork_source_available:
                self._activate_context_usage(cid, sid, initial=False)
                await self.restore_context_usage(cid, sid)
            self._state.session_bound = True
            self._state.fork_source_available = True
            await self._restore_manual_compactions(cid, sid)
            metadata = self._state.snapshot()
            self._history.touch(
                metadata,
                workspace=self.workspace_root,
                source=source,
            )
            observe("conversation.reused", cid=cid, sid=sid, source=source)
            return metadata

        assistant_reply = _last_copyable_assistant_reply(self._history.read_transcript(sid))
        await self.end(reason="switch")
        if workspace_change is not None:
            workspace_change.commit()
        self._state = ConversationState(
            cid=cid,
            sid=sid,
            start_reason=source,
            fork_source_available=True,
        )
        self._lifecycle_id += 1
        self._assistant_reply_snapshot = assistant_reply
        self._activate_context_usage(cid, sid, initial=False)
        metadata = self._state.snapshot()
        self._history.touch(
            metadata,
            workspace=self.workspace_root,
            source=source,
        )
        await self.restore_context_usage(cid, sid)
        await self._restore_manual_compactions(cid, sid)
        observe("conversation.bound", cid=cid, sid=sid, source=source)
        return metadata

    async def end(self, *, reason: SessionEndReason) -> None:
        """结束当前已绑定的根会话生命周期。"""
        recovery = self._compaction_recovery_task
        if recovery is not None:
            recovery.cancel()
            await asyncio.gather(recovery, return_exceptions=True)
        self._pending_compactions.clear()
        self._compaction_scope = None
        self._context_usage.close(clear_listeners=reason == "exit")
        cid = str(self._state.cid or "").strip()
        sid = str(self._state.sid or "").strip()
        if not self._state.session_bound or not valid_session_ids(cid, sid):
            return None

        transcript_path = self._transcript_path_for(sid)
        transcript = self._transcript_factory(
            transcript_path,
            session_id=sid,
        )

        subagent_snapshots = await self._subagent_shutdown(sid)
        await self._finish_end(
            reason=reason,
            cid=cid,
            sid=sid,
            transcript_path=transcript_path,
            transcript=transcript,
            subagent_snapshots=subagent_snapshots,
        )

    async def delete_current(self, request_id: str) -> SessionDeletionResult:
        """协调当前根会话的远端删除、资源收束和本地幂等清理。"""
        async with self._session_deletion_lock:
            cid = str(self._state.cid or "").strip()
            sid = str(self._state.sid or "").strip()
            if not self._state.session_bound or not valid_session_ids(cid, sid):
                return SessionDeletionResult("new_unbound", request_id=request_id)
            if self._deleted_identity == (cid, sid):
                return SessionDeletionResult("already_deleted", request_id=request_id)
            remote = self._session_deletion_remote
            store = self._session_deletion_store
            if remote is None or store is None:
                return SessionDeletionResult(
                    "local_failed", request_id=request_id, code="deletion_unavailable",
                )

            snapshots = await self._subagent_shutdown(sid)
            request = self._deletion_request(request_id, cid, sid, snapshots)
            plan = self._deletion_plan(request, snapshots)
            try:
                receipt = await remote.delete(request)
                _validate_deletion_receipt(request, receipt)
            except asyncio.CancelledError:
                try:
                    store.prepare(plan)
                except Exception as prepare_error:
                    observe_exception(
                        "session.deletion.prepare.cancelled.failed",
                        prepare_error,
                        level="ERROR",
                        cid=cid,
                        sid=sid,
                    )
                raise
            except SessionDeletionRemoteError as error:
                if error.outcome == "unknown":
                    try:
                        await self._await_cleanup(asyncio.to_thread(store.prepare, plan))
                    except asyncio.CancelledError:
                        raise
                    except Exception as prepare_error:
                        observe_exception(
                            "session.deletion.prepare.failed",
                            prepare_error,
                            level="ERROR",
                            cid=cid,
                            sid=sid,
                        )
                        return SessionDeletionResult(
                            "local_failed", request_id=request_id, code="prepare_failed",
                        )
                    return SessionDeletionResult(
                        "unknown", request_id=request_id, code=error.code,
                    )
                return SessionDeletionResult(
                    "rejected", request_id=request_id, code=error.code,
                )

            self._deleted_identity = (cid, sid)
            try:
                await self._await_cleanup(asyncio.to_thread(store.prepare, plan))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                observe_exception(
                    "session.deletion.prepare.failed",
                    error,
                    level="ERROR",
                    cid=cid,
                    sid=sid,
                )
                return SessionDeletionResult(
                    "local_failed", request_id=request_id, code="prepare_failed",
                )

            transcript_path = self._transcript_path_for(sid)
            transcript = self._transcript_factory(transcript_path, session_id=sid)
            if self._session_runtime_close is not None:
                await self._session_runtime_close(sid)
            await self._finish_end(
                reason="deleted",
                cid=cid,
                sid=sid,
                transcript_path=transcript_path,
                transcript=transcript,
                subagent_snapshots=snapshots,
            )
            try:
                await self._await_cleanup(asyncio.to_thread(store.delete, plan))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                observe_exception(
                    "session.deletion.local_cleanup.failed",
                    error,
                    level="ERROR",
                    cid=cid,
                    sid=sid,
                )
                return SessionDeletionResult(
                    "local_failed", request_id=request_id, code="local_cleanup_failed",
                )
            return SessionDeletionResult("deleted", request_id=request_id)

    async def delete_session(
        self,
        cid: str,
        sid: str,
        request_id: str,
    ) -> SessionDeletionResult:
        """按身份进入删除边界，拒绝误删非当前根会话。"""
        if not valid_session_ids(cid, sid):
            return SessionDeletionResult("new_unbound", request_id=request_id)
        if self._deleted_identity == (cid, sid):
            return SessionDeletionResult("already_deleted", request_id=request_id)
        if (self.cid, self.sid) != (cid, sid):
            return SessionDeletionResult("not_current", request_id=request_id)
        return await self.delete_current(request_id)

    async def recover_delete(self, request_id: str) -> SessionDeletionResult:
        """查询尚未确定的原请求，并在完成后继续本地清理。"""
        async with self._session_deletion_lock:
            store = self._session_deletion_store
            remote = self._session_deletion_remote
            if store is None or remote is None:
                return SessionDeletionResult(
                    "local_failed", request_id=request_id, code="deletion_unavailable",
                )
            pending_plans = await asyncio.to_thread(store.pending)
            pending = tuple(
                item for item in pending_plans if item.request_id == request_id
            )
            if not pending:
                return SessionDeletionResult(
                    "unknown", request_id=request_id, code="request_not_found",
                )
            plan = pending[0]
            current_identity = (
                (str(self._state.cid or "").strip(), str(self._state.sid or "").strip())
                if self._state.session_bound
                else None
            )
            request = _request_from_plan(plan, root_identity=current_identity)
            try:
                receipt = await remote.recover(request)
                _validate_deletion_receipt(request, receipt)
            except SessionDeletionRemoteError as error:
                return SessionDeletionResult(
                    error.outcome, request_id=request_id, code=error.code,
                )
            try:
                await self._await_cleanup(asyncio.to_thread(store.delete, plan))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                observe_exception(
                    "session.deletion.local_cleanup.failed",
                    error,
                    level="ERROR",
                    request_id=request_id,
                )
                return SessionDeletionResult(
                    "local_failed", request_id=request_id, code="local_cleanup_failed",
                )
            self._deleted_identity = (plan.targets[0].cid, plan.targets[0].sid)
            return SessionDeletionResult("deleted", request_id=request_id)

    async def archive_current(self) -> dict[str, typing.Any]:
        """归档当前根会话并结束其生命周期。"""
        cid = str(self._state.cid or "").strip()
        sid = str(self._state.sid or "").strip()
        if not self._state.session_bound or not valid_session_ids(cid, sid):
            raise LookupError("conversation session is not started")

        archived = self._history.archive(cid=cid, sid=sid)
        try:
            await self.end(reason="archive")
        except BaseException:
            try:
                self._history.unarchive(cid=cid, sid=sid)
            except Exception as rollback_error:
                observe_exception(
                    "history.archive.rollback.failed",
                    rollback_error,
                    level="ERROR",
                    cid=cid,
                    sid=sid,
                )
            raise
        return archived

    async def archive(
        self,
        cid: str,
        sid: str,
    ) -> dict[str, typing.Any]:
        """把指定的非当前会话迁移到 archived 集合。"""
        if not valid_session_ids(cid, sid):
            raise ValueError("valid cid and sid are required")
        if (str(self._state.cid), str(self._state.sid)) == (str(cid), str(sid)):
            raise ValueError(
                "Use /archive to archive the current session and exit."
            )
        return self._history.archive(cid=cid, sid=sid)


if __name__ == '__main__':
    pass
