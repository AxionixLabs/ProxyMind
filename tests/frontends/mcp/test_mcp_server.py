# -*- coding: utf-8 -*-

import os
import sys
import typing
import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import anyio
import pytest
from metadata import const
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from frontends.mcp.server import (
    McpServerRuntime,
    create_mcp_server,
)
from frontends.mcp import server as mcp_server
from agent.application.turns.projections import RunResultProjection
from agent.application import TurnApplication
from agent.application.turns.run_result import RunResult
from infrastructure.config.paths import ApplicationLayout
from agent.domain.policies import PermissionSettings
from agent.harness.sessions.owner import SessionRuntimeOwner
from agent.harness.hooks.registry import HookRegistry
def _source_layout(tmp_path: Path) -> ApplicationLayout:
    return ApplicationLayout(
        mode="source",
        platform="win32",
        executable=tmp_path / "mind.py",
        root=tmp_path,
        supports=tmp_path / "schematic" / "supports" / "windows",
    )


class _EnvironmentCapability:
    """提供 MCP 入口测试使用的固定环境快照。"""

    def __init__(self) -> None:
        self.clear_cache_mock = Mock()
        self.capture_mock = Mock(return_value={
            "snapshot_id": "envsnap_mcp",
        })

    def capture(self, **kwargs) -> dict[str, object]:
        return self.capture_mock(**kwargs)

    def clear_cache(self) -> None:
        self.clear_cache_mock()


def _environment_snapshot(
    _host: typing.Any,
    *,
    cwd: str | Path,
    workspace_root: str | Path,
) -> dict[str, object]:
    """提供 MCP 入口测试使用的固定环境快照。"""
    _ = (cwd, workspace_root)
    return {"snapshot_id": "envsnap_mcp"}


def _runtime(host: typing.Any, turn_runner: AsyncMock) -> McpServerRuntime:
    """使用指定根轮次用例构造 MCP 测试运行时。"""
    host.runtime_services = _runtime_services()
    host.execution = SimpleNamespace(
        is_service_linked=Mock(return_value=False),
        service_exec_env_snapshot=Mock(return_value=None),
    )
    return McpServerRuntime(
        host,
        report=SimpleNamespace(close=Mock()),
        turn_runner=turn_runner,
        environment_snapshot_provider=_environment_snapshot,
        turn_application=TurnApplication(runtime_factory=SessionRuntimeOwner),
    )


def _runtime_services(model_capability: object | None = None) -> SimpleNamespace:
    """构造 MCP 入口测试使用的进程级依赖。"""
    return SimpleNamespace(
        model_capability=(
            object()
            if model_capability is None
            else model_capability
        ),
        environment_capability=_EnvironmentCapability(),
        create_turn_application=lambda _path: TurnApplication(
            runtime_factory=SessionRuntimeOwner,
        ),
        create_hook_registry=lambda **kwargs: HookRegistry(**kwargs),
    )


def _conversation(
    *,
    reset: AsyncMock | None = None,
    find: Mock | None = None,
    resume: AsyncMock | None = None,
    end: AsyncMock | None = None,
    permissions: PermissionSettings | None = None,
) -> SimpleNamespace:
    """构造 MCP 测试使用的根会话边界。"""
    return SimpleNamespace(
        reset=reset or AsyncMock(),
        history=SimpleNamespace(find=find or Mock(return_value=None)),
        resume=resume or AsyncMock(),
        end=end or AsyncMock(),
        permissions=(
            permissions
            if permissions is not None
            else PermissionSettings("read-only", "on-request")
        ),
    )


def test_mcp_server_exposes_one_structured_tool(tmp_path) -> None:
    server = create_mcp_server(
        layout=_source_layout(tmp_path),
        runtime_services=_runtime_services(),
    )

    tools = server._tool_manager.list_tools()

    assert [tool.name for tool in tools] == ["mind_exec"]
    assert tools[0].parameters["required"] == ["prompt"]
    properties = tools[0].parameters["properties"]
    assert "mode" not in properties
    assert properties["sandbox_mode"]["anyOf"][0]["enum"] == [
        "read-only",
        "workspace-write",
        "danger-full-access",
    ]
    assert properties["approval_policy"]["anyOf"][0]["enum"] == [
        "untrusted",
        "on-request",
        "never",
    ]
    assert properties["timeout_sec"]["default"] == 900.0
    assert properties["session_id"]["default"] is None


@pytest.mark.anyio
async def test_mcp_server_runtime_closes_report_after_runtime_resources(
    tmp_path,
) -> None:
    timeline = []
    host = SimpleNamespace(
        history_workspace=str(tmp_path),
        conversation=_conversation(
            end=AsyncMock(
                side_effect=lambda **_kwargs: timeline.append("session"),
            ),
        ),
        resources=SimpleNamespace(
            close=AsyncMock(side_effect=lambda: timeline.append("resources")),
        ),
    )
    report = SimpleNamespace(
        close=Mock(side_effect=lambda: timeline.append("report")),
    )
    runtime = McpServerRuntime(
        host,
        report=report,
        turn_runner=AsyncMock(),
        turn_application=TurnApplication(runtime_factory=SessionRuntimeOwner),
    )

    await runtime.close()

    assert timeline == ["session", "resources", "report"]


@pytest.mark.anyio
async def test_mcp_server_runtime_releases_resources_when_session_close_fails(
    tmp_path,
) -> None:
    timeline = []
    host = SimpleNamespace(
        history_workspace=str(tmp_path),
        conversation=_conversation(
            end=AsyncMock(side_effect=RuntimeError("session failed")),
        ),
        resources=SimpleNamespace(
            close=AsyncMock(side_effect=lambda: timeline.append("resources")),
        ),
    )
    report = SimpleNamespace(
        close=Mock(side_effect=lambda: timeline.append("report")),
    )
    runtime = McpServerRuntime(
        host,
        report=report,
        turn_runner=AsyncMock(),
        turn_application=TurnApplication(runtime_factory=SessionRuntimeOwner),
    )

    with pytest.raises(RuntimeError, match="session failed"):
        await runtime.close()

    assert timeline == ["resources", "report"]


@pytest.mark.anyio
async def test_mcp_server_runtime_closes_report_when_resource_cleanup_fails(
    tmp_path,
) -> None:
    timeline = []
    host = SimpleNamespace(
        history_workspace=str(tmp_path),
        conversation=_conversation(),
        resources=SimpleNamespace(
            close=AsyncMock(side_effect=RuntimeError("cleanup failed")),
        ),
    )
    report = SimpleNamespace(
        close=Mock(side_effect=lambda: timeline.append("report")),
    )
    runtime = McpServerRuntime(
        host,
        report=report,
        turn_runner=AsyncMock(),
        turn_application=TurnApplication(runtime_factory=SessionRuntimeOwner),
    )

    with pytest.raises(RuntimeError, match="cleanup failed"):
        await runtime.close()

    assert timeline == ["report"]


@pytest.mark.anyio
async def test_mcp_server_runtime_closes_report_when_configuration_fails(
    monkeypatch,
    tmp_path,
) -> None:
    report = SimpleNamespace(close=Mock())

    class FailingConfigSession:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def resolve(self):
            raise RuntimeError("configuration failed")

    monkeypatch.setattr(
        mcp_server,
        "ensure_config_readable",
        lambda: tmp_path / "config.toml",
    )
    monkeypatch.setattr(mcp_server, "ensure_state_home", lambda: tmp_path)
    monkeypatch.setattr(mcp_server, "reports_dir", lambda: tmp_path)
    monkeypatch.setattr(mcp_server, "RunReport", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(mcp_server, "ConfigStore", lambda _path: object())
    monkeypatch.setattr(mcp_server, "ConfigSession", FailingConfigSession)

    with pytest.raises(RuntimeError, match="configuration failed"):
        await McpServerRuntime.open(
            _source_layout(tmp_path),
            runtime_services=_runtime_services(),
        )

    report.close.assert_called_once_with()


@pytest.mark.anyio
async def test_mcp_server_runtime_injects_model_capability(
    monkeypatch,
    tmp_path,
) -> None:
    report = SimpleNamespace(close=Mock())
    config_session = SimpleNamespace(
        resolve=Mock(return_value=SimpleNamespace(config={})),
    )
    pref = SimpleNamespace(load_pref=AsyncMock())
    model_capability = object()
    runtime_services = _runtime_services(model_capability)
    captured: dict[str, typing.Any] = {}
    host = SimpleNamespace(
        history_workspace=str(tmp_path),
        execution=SimpleNamespace(
            external_mcp=SimpleNamespace(start=AsyncMock()),
        ),
        conversation=_conversation(),
        resources=SimpleNamespace(close=AsyncMock()),
    )

    def build_controller(*_args, **kwargs):
        captured.update(kwargs)
        return host

    monkeypatch.setattr(
        mcp_server,
        "ensure_config_readable",
        lambda: tmp_path / "config.toml",
    )
    monkeypatch.setattr(mcp_server, "ensure_state_home", lambda: tmp_path)
    monkeypatch.setattr(mcp_server, "reports_dir", lambda: tmp_path)
    monkeypatch.setattr(mcp_server, "RunReport", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(mcp_server, "ConfigStore", lambda _path: object())
    monkeypatch.setattr(
        mcp_server,
        "ConfigSession",
        lambda *_args, **_kwargs: config_session,
    )
    monkeypatch.setattr(mcp_server, "Preferences", lambda _session: pref)
    monkeypatch.setattr(mcp_server, "route_shell_tools", Mock())
    monkeypatch.setattr(
        mcp_server,
        "ServiceConfig",
        lambda _session: SimpleNamespace(load_domain=AsyncMock(return_value="")),
    )
    monkeypatch.setattr(mcp_server.service_endpoints, "configure", Mock())

    runtime = await McpServerRuntime.open(
        _source_layout(tmp_path),
        runtime_services=runtime_services,
        application_host_factory=build_controller,
    )

    assert captured["runtime_services"] is runtime_services
    runtime_services.environment_capability.clear_cache_mock.assert_called_once_with()
    await runtime.close()


@pytest.mark.anyio
async def test_mcp_server_runtime_executes_isolated_call(tmp_path) -> None:
    result = RunResult(status="completed", assistant_text="done")
    metadata = {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
    }
    turn_runner = AsyncMock(return_value=result)
    host = SimpleNamespace(
        history_workspace=str(tmp_path),
        runtime_services=_runtime_services(),
        execution=SimpleNamespace(
            is_service_linked=Mock(return_value=False),
            service_exec_env_snapshot=Mock(return_value=None),
        ),
        set_history_workspace=Mock(),
        conversation=_conversation(
            reset=AsyncMock(return_value=metadata),
        ),
    )
    runtime = _runtime(host, turn_runner)

    actual = await runtime.execute(
        prompt="inspect",
        sandbox_mode="read-only",
        approval_policy="on-request",
        working_directory=str(tmp_path),
    )

    assert actual.run is result
    assert actual.session_id == metadata["sid"]
    assert actual.projection is not None
    assert actual.to_dict()["assistant_text"] == "done"
    host.set_history_workspace.assert_called_once_with(tmp_path.resolve())
    host.conversation.reset.assert_called_once_with(
        reason="mcp_tool_call",
        source="mcp_server",
    )
    turn_id = turn_runner.await_args.kwargs["turn_id"]
    assert isinstance(turn_id, str) and len(turn_id) == 12
    call_kwargs = dict(turn_runner.await_args.kwargs)
    request_frozen = call_kwargs.pop("on_model_request_frozen")
    assert callable(request_frozen)
    assert turn_runner.await_args.args == (host,)
    assert call_kwargs == {
        "message": "inspect",
        "exec_env": {"snapshot_id": "envsnap_mcp"},
        "permissions": PermissionSettings("read-only", "on-request"),
        "turn_id": turn_id,
    }


@pytest.mark.anyio
async def test_mcp_server_runtime_submits_typed_command_to_application(
    tmp_path,
) -> None:
    result = RunResult(status="completed", assistant_text="done")
    metadata = {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
    }
    turn_runner = AsyncMock(return_value=result)

    class RecordingApplication:
        def __init__(self) -> None:
            self.command = None
            self.closed = False

        async def record_remote_request(self, _command, _request) -> None:
            return None

        async def submit(self, command, executor):
            self.command = command
            value = await executor(command)
            projected_result = value.to_dict()
            projected_result["assistant_text"] = "projected"
            return SimpleNamespace(
                value=value,
                projection=RunResultProjection(
                    status=value.status,
                    exit_code=value.exit_code,
                    result=projected_result,
                ),
            )

        async def close(self, *, cancel_running: bool = False) -> None:
            self.closed = cancel_running

    application = RecordingApplication()
    host = SimpleNamespace(
        history_workspace=str(tmp_path),
        runtime_services=_runtime_services(),
        execution=SimpleNamespace(
            is_service_linked=Mock(return_value=False),
            service_exec_env_snapshot=Mock(return_value=None),
        ),
        set_history_workspace=Mock(),
        conversation=_conversation(
            reset=AsyncMock(return_value=metadata),
        ),
        resources=SimpleNamespace(close=AsyncMock()),
    )
    runtime = McpServerRuntime(
        host,
        report=SimpleNamespace(close=Mock()),
        turn_runner=turn_runner,
        environment_snapshot_provider=_environment_snapshot,
        turn_application=application,
    )

    actual = await runtime.execute(
        prompt="inspect",
        sandbox_mode="read-only",
        approval_policy="on-request",
        working_directory=str(tmp_path),
    )

    assert actual.run is result
    assert application.command.session_id == metadata["sid"]
    assert application.command.message == "inspect"
    assert application.command.environment_snapshot_value() == {
        "snapshot_id": "envsnap_mcp",
    }
    extras = application.command.extras_value()
    assert extras is not None
    turn_id = extras.pop("turn_id")
    assert isinstance(turn_id, str) and len(turn_id) == 12
    assert extras == {
        "working_directory": str(tmp_path.resolve()),
        "sandbox_mode": "read-only",
        "approval_policy": "on-request",
        "approvals_reviewer": "user",
        "network_access": "restricted",
    }
    assert application.command.to_dict()["trace_context"] == {
        "remote_turn": {
            "cid": metadata["cid"],
            "sid": metadata["sid"],
            "turn_id": turn_id,
        },
    }
    assert actual.run.assistant_text == "done"
    assert actual.to_dict()["assistant_text"] == "projected"
    await runtime.close()
    assert application.closed is True


@pytest.mark.anyio
async def test_mcp_server_runtime_uses_default_permissions(tmp_path) -> None:
    metadata = {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
    }
    turn_runner = AsyncMock(return_value=RunResult(status="completed"))
    host = SimpleNamespace(
        history_workspace=str(tmp_path),
        set_history_workspace=Mock(),
        conversation=_conversation(
            reset=AsyncMock(return_value=metadata),
            permissions=PermissionSettings(
                "workspace-write",
                "on-request",
            ),
        ),
    )
    runtime = _runtime(host, turn_runner)

    await runtime.execute(
        prompt="inspect",
        sandbox_mode=None,
        approval_policy=None,
        working_directory=str(tmp_path),
    )

    turn_id = turn_runner.await_args.kwargs["turn_id"]
    assert isinstance(turn_id, str) and len(turn_id) == 12
    call_kwargs = dict(turn_runner.await_args.kwargs)
    request_frozen = call_kwargs.pop("on_model_request_frozen")
    assert callable(request_frozen)
    assert turn_runner.await_args.args == (host,)
    assert call_kwargs == {
        "message": "inspect",
        "exec_env": {"snapshot_id": "envsnap_mcp"},
        "permissions": PermissionSettings("workspace-write", "on-request"),
        "turn_id": turn_id,
    }


@pytest.mark.anyio
async def test_mcp_server_runtime_resumes_workspace_session(tmp_path) -> None:
    result = RunResult(status="completed", assistant_text="continued")
    metadata = {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
    }
    record = {
        **metadata,
        "workspace": str(tmp_path.resolve()),
    }
    turn_runner = AsyncMock(return_value=result)
    host = SimpleNamespace(
        history_workspace=str(tmp_path),
        set_history_workspace=Mock(),
        conversation=_conversation(
            find=Mock(return_value=record),
            resume=AsyncMock(return_value=metadata),
        ),
    )
    runtime = _runtime(host, turn_runner)

    actual = await runtime.execute(
        prompt="continue",
        sandbox_mode="danger-full-access",
        approval_policy="never",
        working_directory=str(tmp_path),
        session_id=metadata["sid"],
    )

    assert actual.run is result
    assert actual.session_id == metadata["sid"]
    host.conversation.reset.assert_not_called()
    host.conversation.history.find.assert_called_once_with(
        metadata["sid"],
        workspace=tmp_path.resolve(),
    )
    host.conversation.resume.assert_called_once_with(
        record,
        source="mcp_server",
    )


@pytest.mark.anyio
async def test_mcp_server_runtime_rejects_unknown_session(tmp_path) -> None:
    turn_runner = AsyncMock()
    host = SimpleNamespace(
        history_workspace=str(tmp_path),
        set_history_workspace=Mock(),
        conversation=_conversation(),
    )
    runtime = _runtime(host, turn_runner)

    actual = await runtime.execute(
        prompt="continue",
        sandbox_mode="read-only",
        approval_policy="on-request",
        working_directory=str(tmp_path),
        session_id="sid_test_1_abcdef",
    )

    assert actual.run.status == "failed"
    assert actual.run.error == "session_id is unavailable for this working directory"
    assert actual.session_id is None
    turn_runner.assert_not_awaited()


@pytest.mark.anyio
async def test_mcp_server_runtime_times_out_and_releases_call_lock(tmp_path) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    metadata = {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
    }

    async def wait_forever(_controller, **_kwargs) -> RunResult:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    turn_runner = AsyncMock(side_effect=wait_forever)
    host = SimpleNamespace(
        history_workspace=str(tmp_path),
        set_history_workspace=Mock(),
        conversation=_conversation(
            reset=AsyncMock(return_value=metadata),
        ),
    )
    runtime = _runtime(host, turn_runner)

    timed_out = await runtime.execute(
        prompt="wait",
        sandbox_mode="read-only",
        approval_policy="on-request",
        working_directory=str(tmp_path),
        timeout_sec=0.01,
    )

    assert started.is_set()
    assert cancelled.is_set()
    assert timed_out.run.status == "failed"
    assert timed_out.run.error == "request timed out after 0.01 seconds"
    assert timed_out.session_id == metadata["sid"]

    turn_runner.side_effect = None
    turn_runner.return_value = RunResult(
        status="completed",
        assistant_text="next",
    )
    following = await runtime.execute(
        prompt="next",
        sandbox_mode="read-only",
        approval_policy="on-request",
        working_directory=str(tmp_path),
    )
    assert following.run.status == "completed"


@pytest.mark.anyio
async def test_mcp_server_runtime_propagates_cancellation(tmp_path) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    metadata = {
        "cid": "cid_test_12345678",
        "sid": "sid_test_1_abcdef",
    }

    async def wait_forever(_controller, **_kwargs) -> RunResult:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    turn_runner = AsyncMock(side_effect=wait_forever)
    host = SimpleNamespace(
        history_workspace=str(tmp_path),
        set_history_workspace=Mock(),
        conversation=_conversation(
            reset=AsyncMock(return_value=metadata),
        ),
    )
    runtime = _runtime(host, turn_runner)

    task = asyncio.create_task(runtime.execute(
        prompt="wait",
        sandbox_mode="read-only",
        approval_policy="on-request",
        working_directory=str(tmp_path),
        timeout_sec=None,
    ))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert cancelled.is_set()


@pytest.mark.anyio
async def test_mcp_stdio_handshake(
    tmp_path: Path,
    repository_root: Path,
) -> None:
    repository = repository_root
    environment = dict(os.environ)
    environment["MIND_HOME"] = str(tmp_path / ".mind")
    environment["HELIX_HOME"] = str(tmp_path / ".mind" / "helix")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(repository / "mind.py"), "mcp-server"],
        cwd=repository,
        env=environment,
    )

    with anyio.fail_after(15):
        async with stdio_client(parameters) as streams:
            async with ClientSession(
                streams[0],
                streams[1],
                read_timeout_seconds=timedelta(seconds=10),
            ) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool("mind_exec", {"prompt": ""})

    assert initialized.serverInfo.name == const.APP_DESC
    assert [tool.name for tool in tools.tools] == ["mind_exec"]
    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["status"] == "failed"
    assert result.structuredContent["error"] == "prompt is empty"
