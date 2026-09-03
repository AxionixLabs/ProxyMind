# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from frontends.cli import bootstrap
from frontends.interaction.contracts import PromptContext
from infrastructure.mcp import external_runtime as external
from frontends.helix import runtime as service_runtime
from infrastructure.mcp import tool_runtime
from infrastructure.mcp.external_runtime import ExternalMcpRuntime
from agent.harness.mcp.owner import McpRuntimeOwner
from agent.harness.process_lifecycle import ProcessLifecycle
from infrastructure.mcp.tool_runtime import CompositeToolRuntime
from agent.ports import (
    McpRuntimeContext,
    McpToolGroupSnapshot,
    ProtocolCommandClient,
    ToolRuntimeSources,
)
from frontends.tui.core.render import fragments_text
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.features import helix
from frontends.tui.session import loop
from agent.domain.policies import preset_permissions
from agent.application import TurnApplication
from agent.harness.sessions.owner import SessionRuntimeOwner


def _mcp_runtime_context(host: object) -> McpRuntimeContext:
    """从测试宿主冻结外部 MCP 所需的最小依赖。"""
    async def no_activity(_snapshot) -> None:
        return None

    async def no_stop(_kind=None, *, settle=True) -> None:
        _ = settle
        return None

    activity = getattr(host, "activity", None)
    lifecycle = getattr(host, "lifecycle", None)

    return McpRuntimeContext(
        config=getattr(
            host,
            "config_session",
            SimpleNamespace(load=lambda: {}),
        ),
        start_activity=getattr(activity, "start_external_mcp", no_activity),
        stop_activity=getattr(activity, "stop", no_stop),
        await_cleanup=(
            lifecycle.await_cleanup
            if lifecycle is not None
            else ProcessLifecycle().await_cleanup
        ),
    )


@pytest.fixture(autouse=True)
def frozen_environment_snapshot(monkeypatch) -> None:
    """固定 TUI 命令提交时捕获的环境事实。"""
    monkeypatch.setattr(
        loop,
        "capture_active_turn_environment",
        Mock(return_value={"snapshot_id": "envsnap_tui"}),
    )


@pytest.fixture(autouse=True)
def injected_turn_application(monkeypatch) -> None:
    """为 TUI 单测注入显式 Session runtime。"""
    monkeypatch.setattr(
        loop,
        "TurnApplication",
        lambda: TurnApplication(runtime_factory=SessionRuntimeOwner),
    )


@pytest.mark.anyio
async def test_query_is_consumed_while_runtime_download_is_active() -> None:
    runtime = TuiRuntime()
    release = asyncio.Event()
    started = asyncio.Event()
    state = {
        "stage": "downloading",
        "filename": "helix.zip",
        "phase": 0.5,
        "done": 5,
        "total": 10,
        "speed": 2,
    }

    async def infrastructure() -> None:
        await runtime.begin_download_status(lambda: dict(state))
        started.set()
        await release.wait()
        state["stage"] = "done"
        await runtime.end_activity_status("download")

    infrastructure_task = runtime.start_background_task(
        infrastructure(),
        name="test infrastructure",
    )
    await started.wait()

    read_task = asyncio.create_task(runtime.read_message(PromptContext(
        model="test-model",
    )))
    runtime.screen.input.buffer.text = "what project is this?"
    runtime.submissions.accept_input(runtime.screen.input.buffer)

    assert await asyncio.wait_for(read_task, timeout=0.2) == "what project is this?"
    assert not infrastructure_task.done()
    assert fragments_text(runtime.document.fragments(width=80)).strip() == (
            "› what project is this?"
    )

    release.set()
    await infrastructure_task
    await runtime.activity.clear()


@pytest.mark.anyio
async def test_tui_loop_reads_query_while_preference_refresh_is_pending(
    monkeypatch,
) -> None:
    runtime = TuiRuntime()
    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()

    async def monitor(_runtime, _host):
        await asyncio.Event().wait()

    class ApplicationStub(object):
        def emit(self, _view):
            return None

    class ApplicationHostStub(object):
        subscription = SimpleNamespace(current=None)
        frontend = SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=ApplicationStub(),
        )

        @staticmethod
        def configuration_service_url() -> str:
            return ""

        def __init__(self) -> None:
            self.lifecycle = ProcessLifecycle()
            self.attach = SimpleNamespace(
                has_pending_attachments=lambda: False,
                pending_attachments_snapshot=lambda: [],
            )
            self.settings = SimpleNamespace(
                permissions=preset_permissions("auto"),
                preference_config=lambda: {
                    "primary": {"model": "test-model"},
                },
                fresh_preferences=self.fresh_preferences,
            )

        async def fresh_preferences(self):
            refresh_started.set()
            await release_refresh.wait()
            return self.settings.preference_config()

    monkeypatch.setattr(loop, "monitor_exec_status", monitor)

    run_task = asyncio.create_task(loop.run_tui_loop(
        ApplicationHostStub(),
        protocol_client=Mock(spec=ProtocolCommandClient),
        turn_runner=AsyncMock(),
    ))
    try:
        await refresh_started.wait()

        runtime.screen.input.buffer.text = "show this immediately"
        runtime.submissions.accept_input(runtime.screen.input.buffer)
        for _ in range(20):
            if runtime.document.blocks:
                break
            await asyncio.sleep(0)

        assert fragments_text(runtime.document.fragments(width=80)).strip() == (
            "› show this immediately"
        )
        assert not run_task.done()
    finally:
        release_refresh.set()
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)
        await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("startup_options", "expected_profile"),
    (
        ({}, "app"),
        ({"tool_profile": "api"}, "api"),
    ),
)
async def test_tui_starts_external_mcp_before_helix_background(
    monkeypatch,
    startup_options,
    expected_profile,
) -> None:
    calls = []
    views = []

    async def prepare_helix(
        _host,
        tool_profile="app",
        *,
        download_confirmed=False,
    ):
        calls.append(("helix", tool_profile, download_confirmed))
        return True

    class ApplicationHostStub(object):
        frontend = SimpleNamespace(
            application=SimpleNamespace(
                emit=views.append,
                viewport=SimpleNamespace(width=80),
            ),
        )

        def __init__(self) -> None:
            runtime = SimpleNamespace(last_start_snapshot={
                "done": True,
                "items": [
                    {"name": "docs", "state": "ready", "tools": 2},
                ],
            })
            self.execution = SimpleNamespace(
                external_mcp=SimpleNamespace(
                    current=runtime,
                    start=self._start_external_mcp,
                ),
                is_service_linked=lambda: False,
            )
            self.activity = SimpleNamespace(stop=self._stop_activity)
            self.lifecycle = ProcessLifecycle()

        async def _start_external_mcp(
            self,
            *,
            defer_activity_stop=False,
        ):
            assert defer_activity_stop
            calls.append(("external", True))

        async def _stop_activity(self, _kind=None, *, settle=True):
            assert not settle

    monkeypatch.setattr(helix, "prepare_tui_service_runtime", prepare_helix)

    host = ApplicationHostStub()
    await bootstrap.start_tui_external_mcp(host)
    await bootstrap.start_tui_service_runtime(host, **startup_options)

    assert calls == [
        ("external", True),
        ("helix", expected_profile, True),
    ]
    status = next(
        view for view in views
        if view.type == "tui.external_mcp.status"
    )
    assert status.renderable.plain_text == (
        "■ External MCP ready · 1/1 servers · 2 tools"
    )
    helix_status = next(
        view for view in views
        if view.type == "tui.helix.status"
    )
    assert helix_status.renderable.plain_text == "■ Helix MCP ready"


@pytest.mark.anyio
async def test_service_runtime_activity_clears_without_settling() -> None:
    activity = SimpleNamespace(
        start_inbuild=AsyncMock(),
        stop=AsyncMock(),
    )
    host = SimpleNamespace(
        activity=activity,
        lifecycle=ProcessLifecycle(),
        service_runtime=SimpleNamespace(
            ensure_ready=AsyncMock(),
            start_keepalive=Mock(),
        ),
    )

    await service_runtime.start_service_runtime(host)

    activity.stop.assert_awaited_once_with("inbuild", settle=False)
    host.service_runtime.start_keepalive.assert_called_once_with()


@pytest.mark.anyio
async def test_external_mcp_concurrent_start_waits_for_first_start(
    monkeypatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    start_called = Mock()
    activity_snapshots = []

    class ExternalGroup(object):
        def __init__(self) -> None:
            self.tools = {
                "mcp__docs__search": SimpleNamespace(meta={
                    "server": "docs",
                    "transport": "stdio",
                    "auth": "None",
                }),
                "mcp__docs__read": SimpleNamespace(meta={
                    "server": "docs",
                    "transport": "stdio",
                    "auth": "None",
                }),
            }
            self.server_stats = {
                "docs": {
                    "server": "docs",
                    "transport": "stdio",
                    "discovered": 3,
                },
            }

        async def start(self, servers, status=None):
            start_called()
            entered.set()
            await release.wait()
            if status is not None:
                status.mark_ready(servers[0], "docs", 2)
                status.finish()
            return 1

        async def close(self):
            return None

    class ApplicationHostStub(object):
        src_opera_place = ""
        stop_calls = []
        config_session = SimpleNamespace(load=lambda: {
            "mcp_servers": {
                "docs": {"command": "docs-server"},
            },
        })

        def __init__(self) -> None:
            self.activity = SimpleNamespace(
                start_external_mcp=self._start_external_mcp,
                stop=self._stop_activity,
            )
            self.lifecycle = ProcessLifecycle()

        async def _start_external_mcp(self, snapshot):
            activity_snapshots.append(snapshot)
            return None

        async def _stop_activity(self, _kind=None, *, settle=True):
            self.stop_calls.append((_kind, settle))
            return None

    monkeypatch.setattr(external, "ExternalMcpGroup", ExternalGroup)

    host = ApplicationHostStub()
    runtime = ExternalMcpRuntime(_mcp_runtime_context(host))
    first = asyncio.create_task(runtime.start())
    second = asyncio.create_task(runtime.start())
    await entered.wait()

    assert not second.done()

    release.set()
    await asyncio.gather(first, second)

    assert start_called.call_count == 1
    assert runtime.group is not None
    snapshot = runtime.last_start_snapshot
    assert snapshot["done"] is True
    assert snapshot["items"] == [{
        "name": "docs",
        "state": "ready",
        "tools": 2,
        "discovered": 2,
        "filtered": 0,
        "detail": "",
    }]
    assert runtime.tool_groups == (McpToolGroupSnapshot(
        server="docs",
        transport="stdio",
        auth="None",
        tools=("mcp__docs__read", "mcp__docs__search"),
        discovered=3,
        exposed=2,
        filtered=1,
    ),)
    assert host.stop_calls == [("external_mcp", False)]
    assert len(activity_snapshots) == 1
    assert isinstance(activity_snapshots[0](), dict)

    await runtime.stop()


@pytest.mark.anyio
async def test_external_mcp_without_connected_group_can_retry(monkeypatch) -> None:
    start_called = Mock()

    class ExternalGroup(object):
        async def start(self, _servers, status=None):
            start_called()
            if status is not None:
                status.finish()
            return 0

        async def close(self):
            return None

    class ApplicationHostStub(object):
        src_opera_place = ""
        config_session = SimpleNamespace(load=lambda: {
            "mcp_servers": {
                "docs": {"command": "docs-server"},
            },
        })

        def __init__(self) -> None:
            self.activity = SimpleNamespace(
                start_external_mcp=self._start_external_mcp,
                stop=self._stop_activity,
            )
            self.lifecycle = ProcessLifecycle()

        async def _start_external_mcp(self, _snapshot):
            return None

        async def _stop_activity(self, _kind=None, *, settle=True):
            _ = settle
            return None

    monkeypatch.setattr(
        external,
        "ExternalMcpGroup",
        ExternalGroup,
    )

    runtime = ExternalMcpRuntime(_mcp_runtime_context(ApplicationHostStub()))
    await runtime.start()
    await runtime.start()

    assert start_called.call_count == 2
    assert not runtime.started
    assert runtime.group is None


@pytest.mark.anyio
async def test_external_owner_reuses_runtime_for_start_and_restart() -> None:
    runtime = SimpleNamespace(
        start=AsyncMock(),
        restart=AsyncMock(),
        stop=AsyncMock(),
    )
    factory = Mock(return_value=runtime)
    host = SimpleNamespace()
    owner = McpRuntimeOwner(runtime_factory=lambda: factory(host))

    await owner.start(include_disabled=True)
    await owner.start()
    await owner.restart(defer_activity_stop=True)

    assert owner.current is runtime
    factory.assert_called_once_with(host)
    assert runtime.start.await_args_list == [
        call(include_disabled=True, defer_activity_stop=False),
        call(include_disabled=False, defer_activity_stop=False),
    ]
    runtime.restart.assert_awaited_once_with(
        include_disabled=False,
        defer_activity_stop=True,
    )


@pytest.mark.anyio
async def test_external_mcp_stop_finishes_cleanup_when_cancelled() -> None:
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    class ExternalGroup(object):
        async def close(self):
            cleanup_started.set()
            await release_cleanup.wait()
            cleanup_finished.set()

    host = SimpleNamespace(lifecycle=ProcessLifecycle())
    runtime = ExternalMcpRuntime(_mcp_runtime_context(host))
    runtime._group = ExternalGroup()
    runtime._started = True

    stop_task = asyncio.create_task(runtime.stop())
    await cleanup_started.wait()
    stop_task.cancel()
    release_cleanup.set()

    with pytest.raises(asyncio.CancelledError):
        await stop_task

    assert cleanup_finished.is_set()
    assert not runtime.started
    assert runtime.group is None


@pytest.mark.anyio
async def test_external_owner_waits_for_runtime_cleanup_when_cancelled() -> None:
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    class RuntimeStub(object):
        async def start(self, **_kwargs) -> None:
            return None

        async def stop(self) -> None:
            cleanup_started.set()
            await release_cleanup.wait()
            cleanup_finished.set()

    owner = McpRuntimeOwner(
        runtime_factory=lambda: RuntimeStub(),
    )
    await owner.start()

    stop_task = asyncio.create_task(owner.close())
    await cleanup_started.wait()
    stop_task.cancel()

    assert not stop_task.done()
    release_cleanup.set()

    with pytest.raises(asyncio.CancelledError):
        await stop_task

    assert cleanup_finished.is_set()
    assert owner.current is None


@pytest.mark.anyio
async def test_model_turn_keeps_external_tool_snapshot_from_session_start(
    monkeypatch,
) -> None:
    build_started = asyncio.Event()
    release_build = asyncio.Event()
    captured_groups = []
    initial_runtime = SimpleNamespace(group=None)
    host = SimpleNamespace(
        external_mcp=SimpleNamespace(current=initial_runtime),
        client_tools=object(),
        is_service_mcp_linked=lambda: False,
    )

    async def build_context(
        *,
        service_session,
        external_group,
        client_registry,
        builtin_registry,
    ):
        _ = (service_session, client_registry, builtin_registry)
        captured_groups.append(external_group)
        build_started.set()
        await release_build.wait()
        return SimpleNamespace(session=object(), tools=[])

    async def user_flow(_session, _tools):
        return None

    monkeypatch.setattr(tool_runtime, "build_tool_context", build_context)

    runtime = CompositeToolRuntime(ToolRuntimeSources(
        client_registry=lambda: host.client_tools,
        builtin_registry=lambda: None,
        external_group=lambda: initial_runtime.group,
        service_linked=host.is_service_mcp_linked,
    ))
    turn = asyncio.create_task(runtime.with_session({}, user_flow))
    await build_started.wait()

    initial_runtime.group = SimpleNamespace(tools={"late": object()})
    release_build.set()
    await turn

    assert captured_groups == [None]
