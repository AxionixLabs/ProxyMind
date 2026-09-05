# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from collections.abc import Mapping

from agent.adapters.protocol.model_request import (
    build_model_stream_request,
    extend_request_context,
)
from agent.adapters.protocol.turn_observation import observe_stream_turn
from agent.application.tools.execution import ToolExecutionAdapter
from agent.application.turns.context import (
    AgentContext,
    TurnContext,
)
from agent.application.turns.durable_queue import (
    DurableQueueApplication,
    DurableQueueSubmissionResult,
    DurableQueueTurnCallbacks,
)
from agent.application.turns.execution import TurnExecution
from agent.application.turns.foreground import run_foreground_turn
from agent.application.turns.run_result import RunResult
from agent.application.turns.transcript import build_turn_input_payload
from agent.domain.policies import (
    PermissionSettings,
    resolve_permissions,
)
from agent.domain.tool_policy import filter_mode_tools
from agent.harness.execution.turn_runner import execute_turn
from agent.harness.hooks.scope import resolve_hook_scope
from agent.harness.hooks.turn_lifecycle import TurnHookEvents
from agent.ports import (
    ApprovalCoordinatorPort,
    EffectJournalFactory,
    EventReportPort,
    ExecutionPolicy,
    ModelCapability,
    OutputSessionFactory,
    PatchPreviewPort,
    RootTurnSessionPort,
    TranscriptFactory,
    TurnCleanupPort,
    TurnExecutionRuntimePort,
    TurnForegroundLifecyclePort,
    ProtocolCommandClient,
    TurnObservationCapability,
    TurnSessionContextPort,
    TurnSessionStatePort,
)
from agent.protocol import (
    LocalDurableQueueSnapshot,
    SubmitTurnCommand,
)

if typing.TYPE_CHECKING:
    from agent.ports import McpSessionPort


async def enqueue_durable_root_turn(
    session: RootTurnSessionPort,
    durable_queue: DurableQueueApplication,
    command: SubmitTurnCommand,
    *,
    permissions: PermissionSettings,
    execution_runtime: TurnExecutionRuntimePort,
    approval_coordinator: ApprovalCoordinatorPort | None = None,
    execution_policy: ExecutionPolicy | None = None,
    transcript_factory: TranscriptFactory | None = None,
    cleanup: TurnCleanupPort | None = None,
    patch_preview: PatchPreviewPort | None = None,
    session_context: TurnSessionContextPort | None = None,
    session_state: TurnSessionStatePort | None = None,
    submission_id: str | None = None,
    client_message_id: str | None = None,
    request_id: str | None = None,
) -> DurableQueueSubmissionResult:
    """在 Queue add 前运行提交 Hook 并冻结完整远端与本地执行语义。"""
    if not isinstance(command, SubmitTurnCommand):
        raise TypeError("durable queue requires SubmitTurnCommand")
    pref_config = command.pref_config_value()
    if pref_config is None:
        raise ValueError("durable queue command requires frozen preferences")
    binding = _command_binding(command)
    _require_current_session(session, binding[0], binding[1])
    execution = _queued_execution(
        session,
        command,
        binding,
        message=command.message,
        pref_config=pref_config,
        permissions=permissions,
        approval_coordinator=approval_coordinator,
        execution_policy=execution_policy,
        transcript_factory=transcript_factory,
        cleanup=cleanup,
        patch_preview=patch_preview,
        session_context=session_context,
        session_state=session_state,
    )

    async def freeze_and_submit(
        _mcp_session: "McpSessionPort",
        tools: list[dict[str, typing.Any]],
    ) -> DurableQueueSubmissionResult:
        """在短生命周期工具会话内冻结模型可见工具目录。"""
        visible_tools = filter_mode_tools(
            execution_runtime.tool_profile_for_turn(),
            tools,
        )
        hook_result = await TurnHookEvents(execution.hook_scope).begin(
            execution.message
        )
        options = _queue_request_options(
            command,
            execution,
            session_context=session_context,
        )
        extend_request_context(
            options,
            additional_context=hook_result.additional_context,
        )
        request = build_model_stream_request(
            execution.context,
            pref_config=pref_config,
            message=hook_result.message,
            tools=visible_tools,
            options=options,
        )
        return await durable_queue.enqueue(
            command,
            request,
            submission_id=submission_id,
            client_message_id=client_message_id,
            request_id=request_id,
        )

    return await execution_runtime.with_mcp_session(
        pref_config,
        freeze_and_submit,
    )


async def observe_durable_root_turn(
    session: RootTurnSessionPort,
    local: LocalDurableQueueSnapshot,
    *,
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
    callbacks: DurableQueueTurnCallbacks | None = None,
) -> RunResult:
    """使用本地冻结语义 attach 并执行已由 queue.start 创建的远端 Turn。"""
    if local.status != "started":
        raise ValueError("durable queue item has not started")
    request = local.request
    _require_current_session(session, request.cid, request.sid)
    permissions = _request_permissions(request.option_values())
    conversation_turn = await session.begin_turn(
        cid=request.cid,
        sid=request.sid,
        title=request.message,
        source="queue:start",
    )
    execution = _observed_execution(
        session,
        local,
        permissions=permissions,
        approval_coordinator=approval_coordinator,
        execution_policy=execution_policy,
        transcript_factory=transcript_factory,
        cleanup=cleanup,
        patch_preview=patch_preview,
        session_context=session_context,
        session_state=session_state,
        additional_context=conversation_turn.additional_context,
        system_message=conversation_turn.system_message,
    )
    resolved_callbacks = callbacks or DurableQueueTurnCallbacks()

    async def execute_observed_turn(
        prepared: TurnExecution,
        mcp_session: "McpSessionPort",
        tools: list[dict[str, typing.Any]],
        report: EventReportPort,
    ) -> RunResult:
        """在当前工具会话中只暴露入队时登记过的工具名称。"""
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


def _queued_execution(
    session: RootTurnSessionPort,
    command: SubmitTurnCommand,
    binding: tuple[str, str, str],
    *,
    message: str,
    pref_config: dict[str, typing.Any],
    permissions: PermissionSettings,
    approval_coordinator: ApprovalCoordinatorPort | None,
    execution_policy: ExecutionPolicy | None,
    transcript_factory: TranscriptFactory | None,
    cleanup: TurnCleanupPort | None,
    patch_preview: PatchPreviewPort | None,
    session_context: TurnSessionContextPort | None,
    session_state: TurnSessionStatePort | None,
) -> TurnExecution:
    """构造尚未启动的 Queue 请求，不推进本地根会话轮次。"""
    cid, sid, turn_id = binding
    context = TurnContext.create(
        agent=AgentContext.root(sid),
        cid=cid,
        sid=sid,
        source="queue:add",
        pref_config=pref_config,
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
        transcript_path=session.transcript_path_for_session(sid),
        turn_id=turn_id,
        session_started=False,
    )
    extras = command.extras_value()
    return TurnExecution(
        context=context,
        message=message,
        hook_scope=resolve_hook_scope(session.hook_scope_provider, context),
        metadata={"cid": cid, "sid": sid},
        input_payload=build_turn_input_payload(
            message,
            attachments=command.attachment_values(),
            extras=extras,
        ),
    )


def _queue_request_options(
    command: SubmitTurnCommand,
    execution: TurnExecution,
    *,
    session_context: TurnSessionContextPort | None,
) -> dict[str, typing.Any]:
    """构造 Queue add 使用且不含运行时回调的请求参数。"""
    options: dict[str, typing.Any] = {
        "attachments": command.attachment_values(),
        "metadata": dict(execution.metadata),
    }
    environment = command.environment_snapshot_value()
    if environment is not None:
        options["exec_env"] = environment
    extras = command.extras_value()
    if extras:
        options["extras"] = extras
    if execution.additional_context:
        options["additional_context"] = list(execution.additional_context)
    if execution.system_message:
        options["system_message"] = execution.system_message
    if isinstance(session_context, TurnSessionContextPort):
        options["skills"] = session_context.skills_payload()
    return options


def _observed_execution(
    session: RootTurnSessionPort,
    local: LocalDurableQueueSnapshot,
    *,
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
    """重建只用于本地观察和工具执行的根 Turn 上下文。"""
    request = local.request
    context = TurnContext.create(
        agent=AgentContext.root(request.sid),
        cid=request.cid,
        sid=request.sid,
        source="queue",
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
    options: Mapping[str, typing.Any],
) -> PermissionSettings:
    """从冻结 AgentRequest 还原客户端本地执行权限。"""
    permissions = options.get("permissions")
    if not isinstance(permissions, Mapping):
        raise ValueError("durable queue request has no frozen permissions")
    return resolve_permissions(dict(permissions), interactive=True)


def _command_binding(command: SubmitTurnCommand) -> tuple[str, str, str]:
    """读取 Queue Command 的严格远端坐标。"""
    remote_turn = command.trace_context.get("remote_turn")
    if not isinstance(remote_turn, Mapping):
        raise ValueError("durable queue command requires remote turn coordinates")
    values: list[str] = []
    for field_name in ("cid", "sid", "turn_id"):
        value = remote_turn.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"durable queue command requires remote {field_name}"
            )
        values.append(value.strip())
    return values[0], values[1], values[2]


def _require_current_session(
    session: RootTurnSessionPort,
    cid: str,
    sid: str,
) -> None:
    """禁止 Queue observer 写入已经切换的本地会话。"""
    current = session.snapshot()
    if current.get("cid") != cid or current.get("sid") != sid:
        raise RuntimeError("durable queue turn belongs to another session")


if __name__ == '__main__':
    pass
