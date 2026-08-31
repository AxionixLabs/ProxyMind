# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mind_app.presentation.output import OutputSession
from agent.application.execution import (
    AgentContext,
    TurnContext,
)
from mind_app.runtime.hooks.runtime import HookRuntime
from agent.application import HookExecutionContext
from mind_app.runtime.hooks.scope import HookExecutionScope
from mind_app.runtime.turns import stream_setup
from mind_app.runtime.turns.executor import TurnExecution
from agent.application import preset_permissions


def _output_session() -> OutputSession:
    """构造不启用 Hook 展示适配的输出会话。"""
    return OutputSession(
        control=Mock(),
        status=Mock(),
        content=Mock(),
        presentation=Mock(),
    )


def _execution() -> TurnExecution:
    """构造具有固定请求上下文的根轮次。"""
    context = TurnContext.create(
        agent=AgentContext.root("sid_test"),
        cid="cid_test",
        sid="sid_test",
        source="test",
        pref_config={"primary": {"model": "test-model"}},
        cwd=".",
        permissions=preset_permissions("auto"),
        output_record_path="output.jsonl",
        turn_id="turn_test",
    )
    return TurnExecution(
        context=context,
        message="hello",
        hook_scope=HookExecutionScope(
            context=HookExecutionContext.from_turn(context),
            dispatcher=HookRuntime.empty(),
        ),
        metadata={"origin": "test"},
        additional_context=("project context",),
        system_message="system context",
    )


def _exec_env_snapshot() -> dict:
    """构造固定且完整的客户端环境快照。"""
    return {
        "snapshot_id": "envsnap_test_snapshot",
        "source": "client",
        "captured_at": "2026-08-29T12:00:00Z",
        "environment_id": "local",
        "cwd": "D:\\PycharmProjects\\ProxyMind",
        "status": "available",
        "status_detail": None,
        "shell": {
            "name": "powershell",
            "syntax": "powershell",
            "executable": "pwsh.exe",
            "prefix": ["pwsh.exe", "-Command"],
            "source": None,
        },
        "workspace": {
            "root": "D:\\PycharmProjects\\ProxyMind",
            "allowed_roots": [],
            "source": "client",
        },
        "tools": {},
        "providers": {},
        "extensions": {},
    }


def _service_environment_provider() -> dict:
    """构造固定的服务端环境能力提供方快照。"""
    return {
        "tools": {},
        "extensions": {},
    }


class _EnvironmentCapability:
    """记录环境捕获调用的能力替身。"""

    def __init__(self, snapshot: dict) -> None:
        self.capture_mock = Mock(return_value=snapshot)

    def capture(self, **kwargs) -> dict:
        return self.capture_mock(**kwargs)

    def clear_cache(self) -> None:
        pass


def _controller(
    session_factory: Mock,
    *,
    retry_state: Mock | None = None,
    environment_snapshot: dict | None = None,
) -> SimpleNamespace:
    """构造准备阶段需要的控制器能力。"""
    environment_capability = _EnvironmentCapability(
        environment_snapshot or _exec_env_snapshot()
    )
    return SimpleNamespace(
        animate=False,
        frontend=SimpleNamespace(
            runtime=SimpleNamespace(
                set_wait_retry_state=retry_state or Mock(),
            ),
            session_factory=session_factory,
        ),
        config_session=SimpleNamespace(load=Mock(return_value={})),
        is_service_mcp_linked=Mock(return_value=True),
        service_exec_env_snapshot=Mock(
            return_value=_service_environment_provider()
        ),
        runtime_services=SimpleNamespace(
            environment_capability=environment_capability,
        ),
        history_workspace="D:\\PycharmProjects\\ProxyMind",
    )


def test_prepare_stream_turn_separates_request_and_continuation_options() -> None:
    output_session = _output_session()
    session_factory = Mock(return_value=output_session)
    input_context = Mock()
    input_event = Mock()
    retry_state = Mock()
    event_report = Mock()
    controller = _controller(session_factory)
    execution = _execution()
    options = {
        "exec_env": _exec_env_snapshot(),
        "skills": [{"name": "test"}],
        "session_factory": session_factory,
        "ev_report": event_report,
        "on_turn_input_context": input_context,
        "on_turn_input_event": input_event,
        "on_retry_state": retry_state,
        "extras": {"trace": "preserved"},
    }

    prepared = stream_setup.prepare_stream_turn(
        controller,
        execution,
        options,
    )

    assert options["session_factory"] is session_factory
    assert prepared.context is execution.context
    assert prepared.message == "hello"
    assert prepared.output_session is output_session
    assert prepared.event_report is event_report
    assert prepared.callbacks.retry_state is retry_state
    assert prepared.request_kwargs == {
        "exec_env": _exec_env_snapshot(),
        "skills": [{"name": "test"}],
        "extras": {"trace": "preserved"},
        "turn_id": "turn_test",
        "permissions": execution.context.permissions,
        "metadata": {
            "origin": "test",
            "cid": "cid_test",
            "sid": "sid_test",
        },
        "additional_context": ["project context"],
        "system_message": "system context",
    }
    assert prepared.continuation_kwargs == options
    assert "turn_id" not in prepared.continuation_kwargs
    input_context.assert_called_once_with(execution.context)
    event_report.begin_turn.assert_called_once_with("turn_test")
    session_factory.assert_called_once_with("output.jsonl", animate=False)


def test_prepare_stream_turn_resolves_missing_request_capabilities(
    monkeypatch,
) -> None:
    output_session = _output_session()
    session_factory = Mock(return_value=output_session)
    retry_state = Mock()
    snapshot = _exec_env_snapshot()
    controller = _controller(
        session_factory,
        retry_state=retry_state,
        environment_snapshot=snapshot,
    )
    build_skills = Mock(return_value=[{"name": "resolved"}])
    monkeypatch.setattr(stream_setup, "skills_payload", build_skills)

    prepared = stream_setup.prepare_stream_turn(
        controller,
        _execution(),
        {},
    )

    assert prepared.request_kwargs["exec_env"] == snapshot
    assert prepared.request_kwargs["skills"] == [{"name": "resolved"}]
    assert prepared.callbacks.retry_state is retry_state
    assert prepared.continuation_kwargs == {
        "exec_env": snapshot,
        "session_factory": session_factory,
    }
    controller.service_exec_env_snapshot.assert_called_once_with()
    environment_capability = (
        controller.runtime_services.environment_capability
    )
    environment_capability.capture_mock.assert_called_once_with(
        cwd=".",
        workspace_root="D:\\PycharmProjects\\ProxyMind",
        providers={"helix": _service_environment_provider()},
    )
    controller.config_session.load.assert_called_once_with()
    build_skills.assert_called_once_with({})


def test_prepare_stream_turn_rejects_non_callable_callback() -> None:
    session_factory = Mock(return_value=_output_session())

    with pytest.raises(
        TypeError,
        match="on_turn_input_event must be callable",
    ):
        stream_setup.prepare_stream_turn(
            _controller(session_factory),
            _execution(),
            {
                "exec_env": _exec_env_snapshot(),
                "skills": [],
                "on_turn_input_event": "invalid",
            },
        )
