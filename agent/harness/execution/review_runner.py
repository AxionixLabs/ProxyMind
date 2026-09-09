# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping

from agent.adapters.protocol.review_events import ReviewEventProjector
from agent.adapters.protocol.turn_source import (
    ObservingReviewTurnStreamSource,
    SubmittingReviewTurnStreamSource,
    TurnStreamSource,
)
from agent.adapters.protocol.turn_stream import stream_turn
from agent.application.hooks.context import HookExecutionContext
from agent.application.tools.execution import ToolExecutionAdapter
from agent.application.turns.context import (
    AgentContext,
    TurnContext,
)
from agent.application.turns.execution import TurnExecution
from agent.application.turns.foreground import run_foreground_turn
from agent.application.turns.observation import TurnObservationCallbacks
from agent.application.turns.run_result import RunResult
from agent.application.turns.transcript import build_turn_input_payload
from agent.domain.policies import PermissionSettings
from agent.harness.execution.turn_runner import execute_turn
from agent.harness.hooks.scope import HookExecutionScope
from agent.ports import (
    ApprovalCoordinatorPort,
    EffectJournalFactory,
    EventReportPort,
    ExecutionPolicy,
    McpSessionPort,
    OutputSessionFactory,
    ProtocolCommandClient,
    ReviewCapability,
    ReviewObservationCapability,
    RootTurnSessionPort,
    TranscriptFactory,
    TurnCleanupPort,
    TurnExecutionRuntimePort,
    TurnForegroundLifecyclePort,
    TurnSessionContextPort,
    TurnSessionStatePort,
)
from agent.protocol import ReviewStreamRequest
from agent.protocol.json_value import ThawedJsonValue


async def run_review_turn(
    session: RootTurnSessionPort,
    pref_config: dict[str, typing.Any],
    request: ReviewStreamRequest,
    environment_snapshot: Mapping[str, ThawedJsonValue] | None,
    *,
    hint: str,
    review_capability: ReviewCapability,
    review_observer: ReviewObservationCapability,
    protocol_client: ProtocolCommandClient,
    effect_journal_factory: EffectJournalFactory,
    tool_execution: ToolExecutionAdapter,
    execution_runtime: TurnExecutionRuntimePort,
    lifecycle: TurnForegroundLifecyclePort,
    approval_coordinator: ApprovalCoordinatorPort | None = None,
    execution_policy: ExecutionPolicy | None = None,
    session_factory: OutputSessionFactory | None = None,
    transcript_factory: TranscriptFactory | None = None,
    cleanup: TurnCleanupPort | None = None,
    session_context: TurnSessionContextPort | None = None,
    session_state: TurnSessionStatePort | None = None,
    callbacks: TurnObservationCallbacks | None = None,
    replay_target_seq: int | None = None,
) -> RunResult:
    """在标准 Turn 事件泵中提交或观察一项只读 Review。"""
    if not isinstance(request, ReviewStreamRequest):
        raise TypeError("review stream request is required")
    if not isinstance(lifecycle, TurnForegroundLifecyclePort):
        raise TypeError("review foreground lifecycle is required")
    _require_current_session(session, request)
    execution = await _prepare_review_execution(
        session,
        pref_config,
        request,
        hint=hint,
        approval_coordinator=approval_coordinator,
        execution_policy=execution_policy,
        transcript_factory=transcript_factory,
        cleanup=cleanup,
        session_context=session_context,
        session_state=session_state,
        register_turn=replay_target_seq is None,
    )
    source: TurnStreamSource
    if replay_target_seq is None:
        source = SubmittingReviewTurnStreamSource(
            review_capability,
            request,
        )
    else:
        source = ObservingReviewTurnStreamSource(
            review_observer,
            request,
            replay_target_seq=replay_target_seq,
        )
    resolved_callbacks = callbacks or TurnObservationCallbacks()
    projector = ReviewEventProjector(
        lifecycle.application,
        hint=hint,
        assistant_reply_sink=(
            session_state.remember_assistant_reply
            if isinstance(session_state, TurnSessionStatePort)
            else None
        ),
    )

    async def execute_review(
        prepared: TurnExecution,
        mcp_session: McpSessionPort,
        tools: list[dict[str, typing.Any]],
        report: EventReportPort,
    ) -> RunResult:
        """使用冻结工具名运行 Review 的共享事件链。"""
        frozen_names = _review_tool_names(request)
        available_names = {
            str(tool.get("name") or "").strip()
            for tool in tools
        }
        missing = frozen_names.difference(available_names)
        if missing:
            names = ", ".join(sorted(missing))
            raise RuntimeError(f"frozen review tools are unavailable: {names}")
        frozen_tools = [
            tool
            for tool in tools
            if str(tool.get("name") or "").strip() in frozen_names
        ]
        stream_options: dict[str, typing.Any] = {
            "turn_source": source,
            "event_projection": projector,
            "protocol_client": protocol_client,
            "effect_journal_factory": effect_journal_factory,
            "tool_execution": tool_execution,
            "skills": (),
        }
        if environment_snapshot is not None:
            stream_options["exec_env"] = dict(environment_snapshot)
        callback_values = {
            "on_turn_input_context": resolved_callbacks.input_context,
            "on_turn_input_event": resolved_callbacks.input_event,
            "on_turn_stream_end": resolved_callbacks.stream_end,
            "on_turn_interrupted": resolved_callbacks.interrupted,
        }
        stream_options.update({
            name: callback
            for name, callback in callback_values.items()
            if callback is not None
        })
        if session_factory is not None:
            stream_options["session_factory"] = session_factory
        return await run_foreground_turn(
            lifecycle,
            stream_turn,
            session=mcp_session,
            pref_config=pref_config,
            tools=frozen_tools,
            turn_execution=prepared,
            ev_report=report,
            **stream_options,
        )

    return await execute_turn(
        execution_runtime,
        pref_config,
        execution,
        execute_review,
        tool_filter_mode="review",
    )


async def _prepare_review_execution(
    session: RootTurnSessionPort,
    pref_config: dict[str, typing.Any],
    request: ReviewStreamRequest,
    *,
    hint: str,
    approval_coordinator: ApprovalCoordinatorPort | None,
    execution_policy: ExecutionPolicy | None,
    transcript_factory: TranscriptFactory | None,
    cleanup: TurnCleanupPort | None,
    session_context: TurnSessionContextPort | None,
    session_state: TurnSessionStatePort | None,
    register_turn: bool,
) -> TurnExecution:
    """按冻结身份创建不带普通 Hook 的 Review 执行上下文。"""
    session_started = False
    session_start_reason = ""
    metadata = _review_metadata(request)
    if register_turn:
        turn = await session.begin_turn(
            cid=request.cid,
            sid=request.sid,
            title=f"Review {hint}",
            source="review",
        )
        metadata.update(turn.metadata())
        session_started = turn.session_started
        session_start_reason = turn.start_reason
    context = TurnContext.create(
        agent=AgentContext.root(request.sid),
        cid=request.cid,
        sid=request.sid,
        source="review" if register_turn else "review:recovery",
        pref_config=pref_config,
        cwd=session.workspace_root,
        permissions=PermissionSettings(
            sandbox_mode="read-only",
            approval_policy="never",
        ),
        permission_grants=session.permission_grants,
        approval_coordinator=approval_coordinator,
        execution_policy=execution_policy,
        approval_ledger=session.approval_ledger,
        transcript_factory=transcript_factory,
        cleanup=cleanup,
        session_context=session_context,
        session_state=session_state,
        output_record_path=session.output_record_path,
        transcript_path=session.transcript_path_for_session(request.sid),
        turn_id=request.turn_id,
        session_started=session_started,
        session_mode=request.session_mode,
        session_start_reason=session_start_reason,
    )
    return TurnExecution(
        context=context,
        message=hint,
        hook_scope=HookExecutionScope.empty(
            HookExecutionContext.from_turn(context),
        ),
        metadata=metadata,
        input_payload=build_turn_input_payload(
            hint,
            extras={"review_target": dict(request.target)},
        ),
    )


def _review_metadata(request: ReviewStreamRequest) -> dict[str, typing.Any]:
    """读取 Review execution 中允许进入本地 Turn 的冻结 metadata。"""
    metadata = request.execution.get("metadata")
    result = dict(metadata) if isinstance(metadata, Mapping) else {}
    result.update({
        "cid": request.cid,
        "sid": request.sid,
        "review_request_id": request.request_id,
    })
    return result


def _review_tool_names(request: ReviewStreamRequest) -> frozenset[str]:
    """从冻结 Review execution 读取唯一允许恢复的工具名。"""
    tools = request.execution.get("tools")
    if not isinstance(tools, tuple):
        raise ValueError("review request does not contain frozen tools")
    names = frozenset(
        name
        for tool in tools
        if isinstance(tool, Mapping)
        for name in [tool.get("name")]
        if isinstance(name, str) and name.strip()
    )
    if not names:
        raise ValueError("review request does not contain frozen tools")
    return frozenset(name.strip() for name in names)


def _require_current_session(
    session: RootTurnSessionPort,
    request: ReviewStreamRequest,
) -> None:
    """禁止 Review 把事件写入已经切换的本地会话。"""
    current = session.snapshot()
    if current.get("cid") != request.cid or current.get("sid") != request.sid:
        raise RuntimeError("review turn belongs to another session")


if __name__ == '__main__':
    pass
