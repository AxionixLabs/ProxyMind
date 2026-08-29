# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import time
import typing
from dataclasses import dataclass
from engine.observability import observe_exception
from mind_core.skills import skills_payload
from mind_nova.events import EventReport
from mind_nova.requests.environment import normalize_client_environment_snapshot
from mind_app.output import (
    OutputSession,
    SessionFactory,
)
from mind_app.runtime.environment.exec_env import build_runtime_exec_env
from mind_app.runtime.execution import TurnContext
from mind_app.runtime.hooks.presentation import HookPresentationAdapter
from mind_app.runtime.hooks.scope import HookExecutionScope
from .executor import TurnExecution

if typing.TYPE_CHECKING:
    from mind_app.controller import Mind


Callback = typing.Callable[..., typing.Any]


@dataclass(frozen=True, slots=True)
class StreamTurnCallbacks:
    """保存单轮流式执行使用的可选生命周期回调。"""

    input_context: Callback | None = None
    input_event: Callback | None = None
    stream_end: Callback | None = None
    interrupted: Callback | None = None
    retry_state: Callback | None = None

    @classmethod
    def take_from(
        cls,
        options: dict[str, typing.Any],
    ) -> "StreamTurnCallbacks":
        """从请求参数移出并校验生命周期回调。"""
        return cls(
            input_context=_optional_callback(
                options.pop("on_turn_input_context", None),
                name="on_turn_input_context",
            ),
            input_event=_optional_callback(
                options.pop("on_turn_input_event", None),
                name="on_turn_input_event",
            ),
            stream_end=_optional_callback(
                options.pop("on_turn_stream_end", None),
                name="on_turn_stream_end",
            ),
            interrupted=_optional_callback(
                options.pop("on_turn_interrupted", None),
                name="on_turn_interrupted",
            ),
            retry_state=_optional_callback(
                options.pop("on_retry_state", None),
                name="on_retry_state",
            ),
        )

    def continuation_kwargs(
        self,
        options: typing.Mapping[str, typing.Any],
    ) -> dict[str, typing.Any]:
        """恢复停止 Hook 续跑时仍需继承的回调参数。"""
        continuation = dict(options)
        callbacks = {
            "on_turn_input_context": self.input_context,
            "on_turn_input_event": self.input_event,
            "on_turn_stream_end": self.stream_end,
            "on_turn_interrupted": self.interrupted,
            "on_retry_state": self.retry_state,
        }
        continuation.update({
            name: callback
            for name, callback in callbacks.items()
            if callback is not None
        })
        return continuation

    def with_retry_state(self, callback: Callback) -> "StreamTurnCallbacks":
        """在调用方未指定时绑定根执行的重试状态出口。"""
        if self.retry_state is not None:
            return self
        return type(self)(
            input_context=self.input_context,
            input_event=self.input_event,
            stream_end=self.stream_end,
            interrupted=self.interrupted,
            retry_state=callback,
        )


@dataclass(frozen=True, slots=True)
class PreparedStreamTurn:
    """固定单轮请求、续跑参数和输出生命周期的准备结果。"""

    context: TurnContext
    hook_scope: HookExecutionScope
    message: str
    callbacks: StreamTurnCallbacks
    started_at: float
    request_kwargs: dict[str, typing.Any]
    continuation_kwargs: dict[str, typing.Any]
    event_report: EventReport | None
    output_session: OutputSession


def prepare_stream_turn(
    controller: "Mind",
    execution: TurnExecution,
    options: typing.Mapping[str, typing.Any],
) -> PreparedStreamTurn:
    """解析并固定一次流式模型执行所需的输入与输出边界。"""
    request_kwargs = dict(options)
    callbacks = StreamTurnCallbacks.take_from(request_kwargs)
    continuation_kwargs = callbacks.continuation_kwargs(request_kwargs)
    started_at = time.perf_counter()
    event_report: EventReport | None = request_kwargs.pop("ev_report", None)

    if not isinstance(execution, TurnExecution):
        raise TypeError("turn_execution is required")

    context = execution.context
    hook_scope = execution.hook_scope

    if callbacks.retry_state is None and context.agent.depth == 0:
        callbacks = callbacks.with_retry_state(
            controller.frontend.runtime.set_wait_retry_state
        )

    if callbacks.input_context is not None:
        callbacks.input_context(context)

    request_kwargs["turn_id"] = context.turn_id
    request_kwargs["permissions"] = context.permissions
    request_kwargs["metadata"] = dict(execution.metadata)

    if execution.additional_context:
        request_kwargs["additional_context"] = list(
            execution.additional_context
        )
    if execution.system_message:
        request_kwargs["system_message"] = execution.system_message

    if event_report:
        event_report.begin_turn(context.turn_id)

    if "exec_env" in request_kwargs:
        raw_exec_env = request_kwargs.get("exec_env")
        if raw_exec_env is None:
            del request_kwargs["exec_env"]
        else:
            request_kwargs["exec_env"] = normalize_client_environment_snapshot(
                raw_exec_env
            )
    else:
        service_env = (
            controller.service_exec_env_snapshot()
            if controller.is_service_mcp_linked()
            else None
        )
        try:
            request_kwargs["exec_env"] = build_runtime_exec_env(
                cwd=context.cwd,
                workspace_root=controller.history_workspace,
                service_exec_env=service_env
            )
        except (OSError, RuntimeError) as error:
            observe_exception(
                "exec_env.capture.failed",
                error,
                level="WARNING",
            )

    if "exec_env" in request_kwargs:
        continuation_kwargs["exec_env"] = copy.deepcopy(
            request_kwargs["exec_env"]
        )

    if request_kwargs.get("skills") is None:
        try:
            skill_config = controller.config_session.load()
        except (OSError, TypeError, ValueError) as error:
            observe_exception(
                "skills.config.failed",
                error,
                level="WARNING",
            )
            skill_config = {}
        request_kwargs["skills"] = skills_payload(skill_config)

    session_factory_value = request_kwargs.pop("session_factory", None)
    if session_factory_value is None:
        frontend = getattr(controller, "frontend", None)
        session_factory_value = getattr(frontend, "session_factory", None)
    session_factory = _resolve_output_session_factory(session_factory_value)
    continuation_kwargs["session_factory"] = session_factory

    output_session = session_factory(
        context.output_record_path,
        animate=bool(getattr(controller, "animate", True)),
    )
    if output_session.show_hook_lifecycle:
        hook_scope = hook_scope.with_default_status_port(
            HookPresentationAdapter(output_session.presentation)
        )

    return PreparedStreamTurn(
        context=context,
        hook_scope=hook_scope,
        message=execution.message,
        callbacks=callbacks,
        started_at=started_at,
        request_kwargs=request_kwargs,
        continuation_kwargs=continuation_kwargs,
        event_report=event_report,
        output_session=output_session,
    )


def _optional_callback(
    value: typing.Any,
    *,
    name: str,
) -> Callback | None:
    """校验可选回调并返回可调用边界。"""
    if value is None:
        return None
    if not callable(value):
        raise TypeError(f"{name} must be callable")
    return value


def _resolve_output_session_factory(value: typing.Any) -> SessionFactory:
    """解析单轮输出工厂并校验续跑边界传入值。"""
    if value is None:
        raise RuntimeError("stream output session factory is required")
    if not callable(value):
        raise TypeError("session_factory must be callable")
    return value


if __name__ == '__main__':
    pass
