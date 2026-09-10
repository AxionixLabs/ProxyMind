# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping

from agent.adapters.protocol.turn_observation import observe_stream_turn
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
from agent.domain.policies import (
    PermissionSettings,
    resolve_permissions,
)
from agent.harness.execution.turn_runner import execute_turn
from agent.harness.hooks.scope import resolve_hook_scope
from agent.ports import (
    ApprovalCoordinatorPort,
    EffectJournalFactory,
    EventReportPort,
    ExecutionPolicy,
    ModelCapability,
    OutputSessionFactory,
    PatchPreviewPort,
    ProtocolCommandClient,
    RootTurnSessionPort,
    TranscriptFactory,
    TurnCleanupPort,
    TurnExecutionRuntimePort,
    TurnForegroundLifecyclePort,
    TurnObservationCapability,
    TurnSessionContextPort,
    TurnSessionStatePort,
)
from agent.protocol import (
    ModelStreamRequest,
    SubmitTurnCommand,
)
from agent.protocol.json_value import ThawedJsonValue

if typing.TYPE_CHECKING:
    from agent.ports import McpSessionPort


async def observe_frozen_root_turn(
    session: RootTurnSessionPort,
    command: SubmitTurnCommand,
    request: ModelStreamRequest,
    *,
    source: str,
    model_capability: ModelCapability,
    turn_observer: TurnObservationCapability,
    protocol_client: ProtocolCommandClient,
    effect_journal_factory: EffectJournalFactory,
    tool_execution: ToolExecutionAdapter,
    approval_coordinator: ApprovalCoordinatorPort | None = None,
    execution_policy: ExecutionPolicy | None = None,
    execution_runtime: TurnExecutionRuntimePort,
    lifecycle: TurnForegroundLifecyclePort | None = None,
    session_factory: OutputSessionFactory | None = None,
    transcript_factory: TranscriptFactory | None = None,
    cleanup: TurnCleanupPort | None = None,
    patch_preview: PatchPreviewPort | None = None,
    session_context: TurnSessionContextPort | None = None,
    session_state: TurnSessionStatePort | None = None,
    callbacks: TurnObservationCallbacks | None = None,
    after_event_seq: int | None = None,
    replay_target_seq: int | None = None,
    records_local_start: bool = True,
) -> RunResult:
    """用完整冻结语义 attach 既有远端 Turn 并恢复本地执行闭环。"""
    if not isinstance(command, SubmitTurnCommand):
        raise TypeError("observed turn command is invalid")
    if not isinstance(request, ModelStreamRequest):
        raise TypeError("observed turn request is invalid")
    _require_current_session(session, request.cid, request.sid)
    options = request.option_values()
    permissions = _request_permissions(options)
    additional_context, system_message = _request_turn_context(options)
    execution = _observed_execution(
        session,
        request,
        source=source,
        permissions=permissions,
        approval_coordinator=approval_coordinator,
        execution_policy=execution_policy,
        transcript_factory=transcript_factory,
        cleanup=cleanup,
        patch_preview=patch_preview,
        session_context=session_context,
        session_state=session_state,
        additional_context=additional_context,
        system_message=system_message,
    )
    resolved_callbacks = callbacks or TurnObservationCallbacks()

    async def execute_observed_turn(
        prepared: TurnExecution,
        mcp_session: "McpSessionPort",
        tools: list[dict[str, typing.Any]],
        report: EventReportPort,
    ) -> RunResult:
        """在当前工具会话中只暴露冻结请求登记过的工具名称。"""
        frozen_names = {
            name
            for item in request.tools
            for name in [item.get("name")]
            if isinstance(name, str) and name
        }
        frozen_tools = [
            tool
            for tool in tools
            if str(tool.get("name") or "") in frozen_names
        ]
        stream_kwargs: dict[str, typing.Any] = {
            "model_capability": model_capability,
            "turn_observer": turn_observer,
            "protocol_client": protocol_client,
            "effect_journal_factory": effect_journal_factory,
            "tool_execution": tool_execution,
            "timeout": request.timeout,
            "observation_after_event_seq": after_event_seq,
            "observation_replay_target_seq": replay_target_seq,
            "observation_records_local_start": records_local_start,
        }
        callback_values = {
            "on_turn_input_context": resolved_callbacks.input_context,
            "on_turn_input_event": resolved_callbacks.input_event,
            "on_turn_stream_end": resolved_callbacks.stream_end,
            "on_turn_interrupted": resolved_callbacks.interrupted,
        }
        stream_kwargs.update({
            name: callback
            for name, callback in callback_values.items()
            if callback is not None
        })
        if session_factory is not None:
            stream_kwargs["session_factory"] = session_factory
        return await run_foreground_turn(
            lifecycle,
            observe_stream_turn,
            session=mcp_session,
            pref_config=request.pref_config_value(),
            tools=frozen_tools,
            turn_execution=prepared,
            ev_report=report,
            **stream_kwargs,
        )

    return await execute_turn(
        execution_runtime,
        request.pref_config_value(),
        execution,
        execute_observed_turn,
    )


def _observed_execution(
    session: RootTurnSessionPort,
    request: ModelStreamRequest,
    *,
    source: str,
    permissions: PermissionSettings,
    approval_coordinator: ApprovalCoordinatorPort | None,
    execution_policy: ExecutionPolicy | None,
    transcript_factory: TranscriptFactory | None,
    cleanup: TurnCleanupPort | None,
    patch_preview: PatchPreviewPort | None,
    session_context: TurnSessionContextPort | None,
    session_state: TurnSessionStatePort | None,
    additional_context: typing.Iterable[str],
    system_message: str,
) -> TurnExecution:
    """重建只用于本地观察、工具执行和终态记录的根 Turn 上下文。"""
    context = TurnContext.create(
        agent=AgentContext.root(request.sid),
        cid=request.cid,
        sid=request.sid,
        source=source,
        pref_config=request.pref_config_value(),
        cwd=session.workspace_root,
        permissions=permissions,
        permission_grants=session.permission_grants,
        approval_coordinator=approval_coordinator,
        execution_policy=execution_policy,
        approval_ledger=session.approval_ledger,
        transcript_factory=transcript_factory,
        cleanup=cleanup,
        patch_preview=patch_preview,
        session_context=session_context,
        session_state=session_state,
        output_record_path=session.output_record_path,
        transcript_path=session.transcript_path_for_session(request.sid),
        turn_id=request.turn_id,
        session_started=False,
    )
    options = request.option_values()
    extras = options.get("extras")
    return TurnExecution(
        context=context,
        message=request.message,
        hook_scope=resolve_hook_scope(session.hook_scope_provider, context),
        metadata={
            **request.metadata_value(),
            "cid": request.cid,
            "sid": request.sid,
        },
        additional_context=tuple(additional_context),
        system_message=system_message,
        input_payload=build_turn_input_payload(
            request.message,
            attachments=request.attachment_values(),
            extras=extras if isinstance(extras, Mapping) else None,
        ),
    )


def _request_permissions(
    options: Mapping[str, ThawedJsonValue],
) -> PermissionSettings:
    """从冻结请求还原客户端本地执行权限。"""
    permissions = options.get("permissions")
    if not isinstance(permissions, Mapping):
        raise ValueError("observed turn request has no frozen permissions")
    return resolve_permissions(dict(permissions), interactive=True)


def _request_turn_context(
    options: Mapping[str, ThawedJsonValue],
) -> tuple[tuple[str, ...], str]:
    """从冻结请求还原本地工具执行使用的单轮上下文。"""
    raw_context = options.get("additional_context", [])
    if not isinstance(raw_context, list) or any(
        not isinstance(value, str) for value in raw_context
    ):
        raise ValueError("observed turn additional_context is invalid")
    raw_system_message = options.get("system_message", "")
    if not isinstance(raw_system_message, str):
        raise ValueError("observed turn system_message is invalid")
    return tuple(raw_context), raw_system_message


def _require_current_session(
    session: RootTurnSessionPort,
    cid: str,
    sid: str,
) -> None:
    """禁止 observer 把恢复事件写入已经切换的本地会话。"""
    current = session.snapshot()
    if current.get("cid") != cid or current.get("sid") != sid:
        raise RuntimeError("observed turn belongs to another session")


if __name__ == '__main__':
    pass
