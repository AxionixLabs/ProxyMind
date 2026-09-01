# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping
from agent.application.turns.run_result import RunResult
from agent.application.turns.execution import TurnExecution
from agent.application.turns.foreground import run_foreground_turn
from agent.application.tools.execution import ToolExecutionAdapter
from agent.ports import (
    EventReportPort,
    ApprovalCoordinatorPort,
    ApprovalLedger,
    EffectJournalFactory,
    ExecutionPolicy,
    ModelCapability,
    ProtocolCommandClient,
    PatchPreviewPort,
    RetryStatePort,
    TurnAnimationPort,
    TurnSessionContextPort,
    TurnSessionStatePort,
    TurnExecutionRuntimePort,
    RootTurnSessionPort,
    TurnForegroundLifecyclePort,
    TurnCleanupPort,
    TranscriptFactory,
)
from agent.application.turns.context import (
    AgentContext,
    TurnContext,
)
from agent.harness.execution.turn_runner import execute_turn
from agent.application.turns.transcript import build_turn_input_payload
from agent.adapters.protocol.turn_stream import stream_turn
from agent.ports import OutputSessionFactory
from agent.domain.policies import PermissionSettings

if typing.TYPE_CHECKING:
    from agent.ports import McpSessionPort


async def prepare_root_turn(
    session: RootTurnSessionPort,
    *,
    message: str,
    title: str,
    source: str,
    pref_config: dict[str, typing.Any],
    permissions: PermissionSettings,
    metadata: Mapping[str, typing.Any],
    attachments: typing.Iterable[Mapping[str, typing.Any]],
    extras: Mapping[str, typing.Any] | None,
    turn_id: str | None,
    approval_ledger: ApprovalLedger | None = None,
    approval_coordinator: ApprovalCoordinatorPort | None = None,
    execution_policy: ExecutionPolicy | None = None,
    transcript_factory: TranscriptFactory | None = None,
    cleanup: TurnCleanupPort | None = None,
    patch_preview: PatchPreviewPort | None = None,
    retry_state: RetryStatePort | None = None,
    animation: TurnAnimationPort | None = None,
    session_context: TurnSessionContextPort | None = None,
    session_state: TurnSessionStatePort | None = None,
) -> TurnExecution:
    """固定根轮次的会话身份、输入快照和执行上下文。"""
    supplied_metadata = dict(metadata)
    conversation_turn = await session.begin_conversation_turn(
        cid=supplied_metadata.get("cid"),
        sid=supplied_metadata.get("sid"),
        title=title,
        source=source,
    )
    canonical_metadata = {
        **supplied_metadata,
        **conversation_turn.metadata(),
    }
    sid = canonical_metadata["sid"]
    context = TurnContext.create(
        agent=AgentContext.root(sid),
        cid=canonical_metadata["cid"],
        sid=sid,
        source=source,
        pref_config=pref_config,
        cwd=session.workspace_root,
        permissions=permissions,
        permission_grants=session.permission_grants,
        approval_coordinator=approval_coordinator,
        execution_policy=execution_policy,
        approval_ledger=approval_ledger,
        transcript_factory=transcript_factory,
        cleanup=cleanup,
        patch_preview=patch_preview,
        retry_state=retry_state,
        animation=animation,
        session_context=session_context,
        session_state=session_state,
        output_record_path=session.output_record_path,
        transcript_path=session.transcript_path_for_session(sid),
        turn_id=turn_id,
        session_started=conversation_turn.session_started,
        session_start_reason=conversation_turn.start_reason,
    )
    return TurnExecution(
        context=context,
        message=message,
        hook_scope=session.hook_scope(context),
        metadata=canonical_metadata,
        additional_context=conversation_turn.additional_context,
        system_message=conversation_turn.system_message,
        input_payload=build_turn_input_payload(
            message,
            attachments=attachments,
            extras=extras,
        ),
    )


async def run_root_turn(
    session: RootTurnSessionPort,
    pref_config: dict[str, typing.Any] | None = None,
    *,
    message: str,
    model_capability: ModelCapability | None = None,
    protocol_client: ProtocolCommandClient | None = None,
    effect_journal_factory: EffectJournalFactory | None = None,
    tool_execution: ToolExecutionAdapter,
    approval_coordinator: ApprovalCoordinatorPort | None = None,
    execution_policy: ExecutionPolicy | None = None,
    execution_runtime: TurnExecutionRuntimePort,
    lifecycle: TurnForegroundLifecyclePort | None = None,
    session_factory: OutputSessionFactory | None = None,
    transcript_factory: TranscriptFactory | None = None,
    cleanup: TurnCleanupPort | None = None,
    patch_preview: PatchPreviewPort | None = None,
    retry_state: RetryStatePort | None = None,
    animation: TurnAnimationPort | None = None,
    session_context: TurnSessionContextPort | None = None,
    session_state: TurnSessionStatePort | None = None,
    **kwargs: typing.Any,
) -> RunResult:
    """准备根轮次并通过主前端生命周期执行。"""
    if not str(message or "").strip():
        return RunResult(status="failed", error="message is empty")

    if pref_config is None:
        pref_config = await session.fresh_pref_config(ttl_sec=0.0)

    permissions = kwargs.pop("permissions", None) or session.permissions
    source = str(kwargs.pop("source", "calling") or "calling").strip()
    title = str(kwargs.pop("title", message) or message).strip()
    raw_metadata = kwargs.pop("metadata", None)
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    raw_attachments = kwargs.get("attachments")
    attachments = (
        tuple(item for item in raw_attachments if isinstance(item, dict))
        if isinstance(raw_attachments, (list, tuple))
        else ()
    )
    raw_extras = kwargs.get("extras")
    execution = await prepare_root_turn(
        session,
        message=message,
        title=title,
        source=source,
        pref_config=pref_config,
        permissions=permissions,
        metadata=metadata,
        attachments=attachments,
        extras=raw_extras if isinstance(raw_extras, dict) else None,
        turn_id=kwargs.pop("turn_id", None),
        approval_ledger=session.approval_ledger,
        approval_coordinator=approval_coordinator,
        execution_policy=execution_policy,
        transcript_factory=transcript_factory,
        cleanup=cleanup,
        patch_preview=patch_preview,
        retry_state=retry_state,
        animation=animation,
        session_context=session_context,
        session_state=session_state,
    )
    event_report = kwargs.pop("ev_report", None)

    async def execute_prepared_turn(
        prepared: TurnExecution,
        session: "McpSessionPort",
        tools: list[dict[str, typing.Any]],
        report: EventReportPort,
    ) -> RunResult:
        """使用主前端生命周期执行已经准备好的根轮次。"""
        stream_kwargs: dict[str, typing.Any] = dict(kwargs)
        if model_capability is not None:
            stream_kwargs["model_capability"] = model_capability
        if protocol_client is not None:
            stream_kwargs["protocol_client"] = protocol_client
        if effect_journal_factory is not None:
            stream_kwargs["effect_journal_factory"] = effect_journal_factory
        stream_kwargs["tool_execution"] = tool_execution
        if session_factory is not None:
            stream_kwargs["session_factory"] = session_factory
        return await run_foreground_turn(
            lifecycle,
            stream_turn,
            session=session,
            pref_config=pref_config,
            tools=tools,
            turn_execution=prepared,
            ev_report=report,
            **stream_kwargs,
        )

    return await execute_turn(
        execution_runtime,
        pref_config,
        execution,
        execute_prepared_turn,
        event_report=event_report,
    )


if __name__ == '__main__':
    pass
