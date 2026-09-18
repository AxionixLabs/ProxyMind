import asyncio
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from agent.application.config.session_identity import derive_local_session_id
from agent.application.agents.thread import AgentThreadContext
from agent.application.turns.context import AgentContext
from agent.composition import open_turn_application
from agent.domain.approvals import (
    ActionFingerprint,
    ApprovalIdentity,
    CommandApprovalAction,
    ExecutionIdentity,
)
from agent.domain.policies import preset_permissions
from agent.ports.session_deletion import (
    LocalDeletionPlan,
    LocalDeletionTarget,
)
from agent.protocol import (
    ModelStreamRequest,
    SubmitTurnCommand,
)
from agent.protocol.context_usage import (
    ContextUsageRecord,
    SessionTokenUsageRecord,
)
from agent.protocol.json_value import ThawedJsonValue
from agent.stores.agents.graph import (
    AgentGraphCheckpoint,
    AgentGraphRecord,
    AgentGraphStore,
)
from agent.stores.agents.mailbox import AgentMailboxStore
from agent.stores.approvals.facts import SQLiteApprovalFactStore
from agent.stores.effects.journal import LocalEffectJournal
from agent.stores.runs.store import SQLiteRunStore
from agent.stores.sessions.deletion import SQLiteSessionDeletionStore
from agent.stores.sessions.history import ConversationHistoryStore
from infrastructure.persistence.transcripts import ConversationTranscriptStore
from protocol.schema.identifiers import (
    new_cid,
    new_sid,
)
from protocol.schema.stream_events import ExecutionEffect


def target() -> LocalDeletionTarget:
    """创建本轮隔离数据使用的合法会话坐标。"""
    cid = new_cid()
    return LocalDeletionTarget(cid, new_sid(cid), ())


def store(directory: Path, owner: LocalDeletionTarget) -> SQLiteSessionDeletionStore:
    """在显式验收目录组合全部真实存储。"""
    return SQLiteSessionDeletionStore(
        history=ConversationHistoryStore(directory / "history.db"),
        graphs=AgentGraphStore(directory / "agents.db"),
        runs=SQLiteRunStore(directory / "runtime.db"),
        effects=LocalEffectJournal(directory / "effects.db", cid=owner.cid, sid=owner.sid),
        approvals=SQLiteApprovalFactStore(directory / "approvals.db"),
        transcripts=ConversationTranscriptStore(directory / "sessions"),
    )


def command(
    owner: LocalDeletionTarget, *, suffix: str = "", local_session_id: str | None = None,
) -> SubmitTurnCommand:
    """冻结能够证明远端归属的本地运行命令。"""
    return SubmitTurnCommand.create(
        session_id=local_session_id or derive_local_session_id("tui", {"cid": owner.cid, "sid": owner.sid}),
        run_id="run_" + owner.sid + suffix, command_id="command_" + owner.sid + suffix,
        idempotency_key="intent_" + owner.sid + suffix, message="isolated acceptance",
        trace_context={"remote_turn": {"cid": owner.cid, "sid": owner.sid, "turn_id": "turn_" + owner.sid}},
    )


def effect(owner: LocalDeletionTarget, *, suffix: str = "") -> ExecutionEffect:
    """创建与本地 Run outbox 身份不同的工具效果。"""
    return ExecutionEffect(effect_id="effect_" + owner.sid + suffix, fingerprint="a" * 64, replay="manual")


def approval(owner: LocalDeletionTarget) -> CommandApprovalAction:
    """创建绑定线上会话的真实审批事实输入。"""
    return CommandApprovalAction(
        identity=ApprovalIdentity(session_id=owner.sid, run_id="turn_" + owner.sid,
                                  approval_id="approval_one", action_id="action_one"),
        execution=ExecutionIdentity(environment_id="workspace-write", execution_id="execution_one", tool_call_id="call_one"),
        fingerprint=ActionFingerprint("a" * 64), command=("echo", "acceptance"), cwd="/workspace",
    )


@dataclass(frozen=True)
class _Result:
    """提供 Run 终态投影所需的完整结果。"""
    status: str = "completed"

    def to_dict(self) -> dict[str, ThawedJsonValue]:
        """生成包括工具、审批和证据依赖的终态事实。"""
        return {"status": self.status, "assistant_text": "isolated answer", "exit_code": 0,
                "error": None, "usage": {}, "tool_results": [{"ok": True}],
                "approval_decisions": [{"decision": "accept"}], "evidence_references": [{"path": "fixture"}]}


async def seed(directory: Path, owner: LocalDeletionTarget) -> None:
    """通过公开持久化入口创建真实会话数据。"""
    backend = store(directory, owner)
    backend.history.touch_session(cid=owner.cid, sid=owner.sid, title="isolated acceptance", source="tui")
    backend.history.get_or_create_fork_request(cid=owner.cid, sid=owner.sid, request_id="fork_" + owner.sid)
    backend.history.save_context_usage(ContextUsageRecord(
        owner.cid, owner.sid, "turn_" + owner.sid, 1, 1, 32000, 12,
        SessionTokenUsageRecord(12, 10, 5, 0, 2, None, 1, 0), "provider", "test-model", "test-route",
    ))
    backend.graphs.save(AgentGraphCheckpoint(owner.sid, 1, time.time_ns() // 1_000_000))
    await backend.approvals.record_requested(approval(owner))
    await backend.effects.begin(effect(owner))
    await backend.effects.commit(effect(owner), {"answer": "isolated effect"})
    await backend.effects.save_tool_result(owner.cid, owner.sid, "call_one", {"ok": True}, {"ok": True})
    application = open_turn_application(directory / "runtime.db")
    request = command(owner)

    async def execute(submitted: SubmitTurnCommand) -> _Result:
        await backend.runs.save_remote_request(submitted, ModelStreamRequest(
            cid=owner.cid, sid=owner.sid, turn_id="turn_" + owner.sid,
            pref_config={"primary": {"model": "test-model"}}, message="isolated acceptance", tools=(),
        ))
        return _Result()

    try:
        await application.submit(request, execute)
    finally:
        await application.close()
    transcripts = ConversationTranscriptStore(directory / "sessions")
    path = transcripts.path_for_session(owner.sid)
    assert path
    writer = transcripts.writer(path, session_id=owner.sid)
    writer.open()
    writer.append("acceptance", payload={"text": "isolated transcript"})
    writer.close()
    assert transcripts.reader(path).read()


def rows(directory: Path) -> dict[str, int]:
    """重新打开数据库并核对数据量、完整性和外键。"""
    tables = {
        "history": ("conversation_session_cursors", "conversation_context_usage", "conversation_pending_forks"),
        "agents": ("agent_graph_checkpoints",),
        "effects": ("local_effects", "local_tool_results"),
        "runtime": ("run_events", "run_snapshots", "run_remote_requests", "run_outbox", "run_facts"),
        "approvals": ("approval_facts",),
    }
    counts: dict[str, int] = {}
    for database, names in tables.items():
        with sqlite3.connect(directory / f"{database}.db") as connection:
            for table in names:
                counts[table] = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    return counts


def seeded(directory: Path) -> tuple[LocalDeletionPlan, LocalDeletionTarget]:
    """创建含真实子代理关系和邮箱的删除集合与无关对照会话。"""
    targets = (target(), target())
    control = target()
    for owner in (*targets, control):
        asyncio.run(seed(directory, owner))
    root, child = targets
    thread = AgentThreadContext(
        agent=AgentContext.root(root.sid).child("worker", "worker", agent_id="acceptance_worker"),
        cid=child.cid, sid=child.sid, source="subagent", cwd=str(directory),
        permissions=preset_permissions("auto"), pref_config={"primary": {"model": "fixture"}},
        spawn_turn_id="turn_" + root.sid,
    )
    mailbox = AgentMailboxStore()
    mailbox.publish("message", AgentContext.root(root.sid), recipient=thread.agent, message="isolated child mailbox")
    store(directory, root).graphs.save(AgentGraphCheckpoint(
        root.sid, 2, time.time_ns() // 1_000_000,
        records=(AgentGraphRecord(thread=thread, status="completed"),), mailbox=mailbox.snapshot(),
    ))
    return LocalDeletionPlan("delete_storage_acceptance", targets, targets[0]), control
