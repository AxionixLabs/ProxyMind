# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import contextlib
import typing
from collections.abc import (
    Awaitable,
    Callable,
    Iterable,
)
from pathlib import Path

from agent.application.agents.views import AgentSnapshot
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
from agent.ports.conversation import ContextUsageFeed
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


class RootConversationSession:
    """拥有根会话状态、历史游标、Transcript 和结束事务。"""

    def __init__(
        self,
        history: ConversationHistoryPort,
        *,
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
    ) -> None:
        self._history = history
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
        self._state = ConversationState()
        self._lifecycle_id = 0
        self._assistant_reply_snapshot: AssistantReplySnapshot | None = None
        self._context_usage = ContextUsageProjection()

    @property
    def context_usage(self) -> ContextUsageFeed:
        """提供跨 Turn 保留的用量展示订阅。"""
        return self._context_usage

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

    def _activate_context_usage(self, cid: str, sid: str, *, initial: bool) -> None:
        """切换活动投影；已有会话先进入 pending，再恢复精确缓存。"""
        self._context_usage.activate(cid, sid, initial=initial)
        if not initial:
            record = self._history.load_context_usage(cid, sid)
            if record is not None:
                self._context_usage.apply(record)
            self._context_usage.finish_replay(cid, sid)

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

    def snapshot(self) -> dict[str, str]:
        """返回当前会话的稳定身份快照。"""
        unbound = self._state.cid is None or self._state.sid is None
        metadata = self._state.snapshot()
        if unbound:
            self._activate_context_usage(metadata["cid"], metadata["sid"], initial=True)
        return metadata

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
        if self._state.cid == cid and self._state.sid == sid and workspace_change is None:
            if not self._state.fork_source_available:
                self._activate_context_usage(cid, sid, initial=False)
            self._state.session_bound = True
            self._state.fork_source_available = True
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
        observe("conversation.bound", cid=cid, sid=sid, source=source)
        return metadata

    async def end(self, *, reason: SessionEndReason) -> None:
        """结束当前已绑定的根会话生命周期。"""
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

        subagent_snapshots = await self._subagent_shutdown(sid)
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


if __name__ == '__main__':
    pass
