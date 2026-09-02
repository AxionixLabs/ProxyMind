# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.ports import (
    OutputSession,
    OutputSurfaceContext,
    PassiveOutputActivity,
)
from agent.application.turns.context import (
    AgentContext,
    TurnContext,
)
from agent.harness.hooks.runtime import HookRuntime
from agent.application.hooks.context import HookExecutionContext
from agent.application.turns.execution import TurnExecution
from agent.harness.hooks.scope import HookExecutionScope
from agent.adapters.protocol import turn_setup
from agent.domain.policies import preset_permissions


def _output_session(context: OutputSurfaceContext) -> OutputSession:
    """构造不启用 Hook 展示适配的输出会话。"""
    return OutputSession(
        context=context,
        control=Mock(),
        activity=PassiveOutputActivity(),
        content=Mock(),
        presentation=Mock(),
    )


class _SessionContext:
    """实现 TurnSessionContextPort 的测试替身。"""

    def __init__(self, snapshot: dict | None = None) -> None:
        self.capture_mock = Mock(return_value=snapshot)
        self.skills_mock = Mock(return_value=[{"name": "resolved"}])

    @property
    def animate(self) -> bool:
        return False

    @property
    def workspace_root(self) -> str:
        return "."

    @property
    def hook_startup_warnings(self) -> tuple[str, ...]:
        return ()

    @property
    def command_hook_sessions(self):
        return None

    def capture_environment(self, **kwargs) -> dict | None:
        return self.capture_mock(**kwargs)

    def skills_payload(self) -> list[dict]:
        return self.skills_mock()


def _execution(*, session_context=None) -> TurnExecution:
    """构造具有固定请求上下文的根轮次。"""
    if session_context is None:
        session_context = _SessionContext()
    context = TurnContext.create(
        agent=AgentContext.root("sid_test"),
        cid="cid_test",
        sid="sid_test",
        source="test",
        pref_config={"primary": {"model": "test-model"}},
        cwd=".",
        permissions=preset_permissions("auto"),
        session_context=session_context,
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


def _session_context(
    *,
    environment_snapshot: dict | None = None,
) -> _SessionContext:
    """构造准备阶段需要的会话上下文能力。"""
    return _SessionContext(
        environment_snapshot or _exec_env_snapshot(),
    )


def test_prepare_stream_turn_separates_request_and_continuation_options() -> None:
    session_factory = Mock(side_effect=lambda _path, *, context, animate: (
        _output_session(context)
    ))
    input_context = Mock()
    input_event = Mock()
    event_report = Mock()
    execution = _execution()
    options = {
        "exec_env": _exec_env_snapshot(),
        "skills": [{"name": "test"}],
        "session_factory": session_factory,
        "ev_report": event_report,
        "on_turn_input_context": input_context,
        "on_turn_input_event": input_event,
        "extras": {"trace": "preserved"},
    }

    prepared = turn_setup.prepare_stream_turn(
        execution,
        options,
    )

    assert options["session_factory"] is session_factory
    assert prepared.context is execution.context
    assert prepared.message == "hello"
    assert prepared.output_session.context.cid == "cid_test"
    assert prepared.output_session.context.sid == "sid_test"
    assert prepared.output_session.context.turn_id == "turn_test"
    assert prepared.output_session.context.agent_id == "root"
    assert prepared.output_session.context.surface_id.startswith("surface_")
    assert prepared.event_report is event_report
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
    session_factory.assert_called_once_with(
        "output.jsonl",
        context=prepared.output_session.context,
        animate=False,
    )


def test_prepare_stream_turn_resolves_missing_request_capabilities() -> None:
    session_factory = Mock(side_effect=lambda _path, *, context, animate: (
        _output_session(context)
    ))
    snapshot = _exec_env_snapshot()
    session_context = _session_context(
        environment_snapshot=snapshot,
    )

    prepared = turn_setup.prepare_stream_turn(
        _execution(session_context=session_context),
        {"session_factory": session_factory},
    )

    assert prepared.request_kwargs["exec_env"] == snapshot
    assert prepared.request_kwargs["skills"] == [{"name": "resolved"}]
    assert prepared.continuation_kwargs == {
        "exec_env": snapshot,
        "session_factory": session_factory,
    }
    session_context.skills_mock.assert_called_once_with()
    session_context.capture_mock.assert_called_once_with(
        cwd=".",
        workspace_root=".",
    )
    assert session_context.skills_mock.call_count == 1


def test_prepare_stream_turn_rejects_missing_session_factory() -> None:
    with pytest.raises(
        RuntimeError,
        match="stream output session factory is required",
    ):
        turn_setup.prepare_stream_turn(
            _execution(),
            {
                "exec_env": _exec_env_snapshot(),
                "skills": [],
            },
        )


def test_prepare_stream_turn_rejects_non_callable_callback() -> None:
    session_factory = Mock()

    with pytest.raises(
        TypeError,
        match="on_turn_input_event must be callable",
    ):
        turn_setup.prepare_stream_turn(
            _execution(),
            {
                "exec_env": _exec_env_snapshot(),
                "skills": [],
                "on_turn_input_event": "invalid",
            },
        )
