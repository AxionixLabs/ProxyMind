# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from collections.abc import Mapping
from protocol.transport.events import EventReport
from agent.application import SubmitTurnCommand
from mind_app.runtime.execution import (
    AgentContext,
    TurnContext,
)
from mind_app.runtime.turns.executor import (
    TurnExecution,
    build_turn_input_payload,
    execute_turn,
    resolve_turn_hook_scope,
)
from mind_app.runtime.turns.result import RunResult
from mind_app.runtime.turns.stream import stream_turn
from mind_app.presentation.stream.worked import emit_worked_footer
from agent.application import PermissionSettings

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind
    from mind_app.mcp.contracts import McpSessionLike


class RootTurnRunner(typing.Protocol):
    """定义提交根轮次请求所需的应用用例。"""

    async def __call__(
        self,
        controller: "Mind",
        pref_config: dict[str, typing.Any] | None = None,
        *,
        message: str,
        **kwargs: typing.Any,
    ) -> RunResult:
        """准备并执行一次根轮次。"""
        ...


async def run_foreground_turn(
    controller: "Mind",
    operation: typing.Callable[..., typing.Awaitable[RunResult]],
    *args: typing.Any,
    **kwargs: typing.Any,
) -> RunResult:
    """在主前端进度和动画生命周期内执行一次轮次操作。"""
    started_at = time.perf_counter()
    frontend_runtime = controller.frontend.runtime
    frontend_runtime.begin_terminal_progress()
    completed = False

    try:
        await controller.start_anim()
        result = await operation(*args, **kwargs)
        completed = True
        return result
    finally:
        try:
            if completed:
                finish_turn_wait = getattr(
                    frontend_runtime,
                    "finish_turn_wait",
                    None,
                )
                if callable(finish_turn_wait):
                    finish_turn_wait()
            if completed and controller.animate:
                emit_worked_footer(
                    controller.frontend.application,
                    time.perf_counter() - started_at,
                )
        finally:
            try:
                await controller.await_cleanup(controller.stop_anim("wait"))
            finally:
                frontend_runtime.end_terminal_progress()


async def prepare_root_turn(
    controller: "Mind",
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
) -> TurnExecution:
    """固定根轮次的会话身份、输入快照和执行上下文。"""
    supplied_metadata = dict(metadata)
    conversation_turn = await controller.begin_conversation_turn(
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
        cwd=controller.history_workspace,
        permissions=permissions,
        permission_grants=getattr(controller, "permission_grants", None),
        output_record_path=str(controller.report.output_record_path or ""),
        transcript_path=controller.transcripts.path_for_session(sid),
        turn_id=turn_id,
        session_started=conversation_turn.session_started,
        session_start_reason=conversation_turn.start_reason,
    )
    return TurnExecution(
        context=context,
        message=message,
        hook_scope=resolve_turn_hook_scope(controller, context),
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
    controller: "Mind",
    pref_config: dict[str, typing.Any] | None = None,
    *,
    message: str,
    **kwargs: typing.Any,
) -> RunResult:
    """准备根轮次并通过主前端生命周期执行。"""
    if not str(message or "").strip():
        return RunResult(status="failed", error="message is empty")

    if pref_config is None:
        pref_config = await controller.fresh_pref_config(ttl_sec=0.0)

    permissions = kwargs.pop("permissions", None) or controller.permissions
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
        controller,
        message=message,
        title=message,
        source="calling",
        pref_config=pref_config,
        permissions=permissions,
        metadata=metadata,
        attachments=attachments,
        extras=raw_extras if isinstance(raw_extras, dict) else None,
        turn_id=kwargs.pop("turn_id", None),
    )
    event_report = kwargs.pop("ev_report", None)

    async def execute_prepared_turn(
        prepared: TurnExecution,
        session: "McpSessionLike",
        tools: list[dict[str, typing.Any]],
        report: EventReport,
    ) -> RunResult:
        """使用主前端生命周期执行已经准备好的根轮次。"""
        return await run_foreground_turn(
            controller,
            stream_turn,
            controller,
            session=session,
            pref_config=pref_config,
            tools=tools,
            turn_execution=prepared,
            ev_report=report,
            **kwargs,
        )

    return await execute_turn(
        controller,
        pref_config,
        execution,
        execute_prepared_turn,
        event_report=event_report,
    )


class RootTurnCommandExecutor:
    """把冻结的主动 Turn 命令适配到现有根轮次执行器。

    该适配器只拥有命令字段到旧执行器参数的映射，不创建 Session、Run 或
    前端状态；调用方负责将其实例注入 `TurnApplication`，从而保持入口和
    Harness 生命周期解耦。
    """

    def __init__(
        self,
        controller: "Mind",
        *,
        turn_runner: RootTurnRunner | None = None,
        permissions: PermissionSettings | None = None,
        include_empty_attachments: bool = False,
    ) -> None:
        """绑定控制器、可替换执行器和可选的权限覆盖。"""
        if not isinstance(include_empty_attachments, bool):
            raise TypeError("include_empty_attachments must be boolean")
        self._controller = controller
        self._turn_runner = (
            run_root_turn if turn_runner is None else turn_runner
        )
        self._permissions = permissions
        self._include_empty_attachments = include_empty_attachments

    async def __call__(self, command: SubmitTurnCommand) -> RunResult:
        """解包冻结命令并执行一次根轮次。"""
        if not isinstance(command, SubmitTurnCommand):
            raise TypeError("root turn command executor requires SubmitTurnCommand")

        root_kwargs: dict[str, typing.Any] = {
            "exec_env": command.environment_snapshot_value(),
        }

        pref_config = command.pref_config_value()
        if pref_config is not None:
            root_kwargs["pref_config"] = pref_config

        attachments = command.attachment_values()
        if attachments or self._include_empty_attachments:
            root_kwargs["attachments"] = attachments

        if self._permissions is not None:
            root_kwargs["permissions"] = self._permissions

        values = command.extras_value() or {}
        metadata = values.get("metadata")
        if isinstance(metadata, Mapping):
            root_kwargs["metadata"] = dict(metadata)

        request_extras = values.get("request_extras")
        if isinstance(request_extras, Mapping) and request_extras:
            root_kwargs["extras"] = dict(request_extras)

        turn_id = values.get("turn_id")
        if isinstance(turn_id, str) and turn_id.strip():
            root_kwargs["turn_id"] = turn_id

        return await self._turn_runner(
            self._controller,
            message=command.message,
            **root_kwargs,
        )


if __name__ == '__main__':
    pass
