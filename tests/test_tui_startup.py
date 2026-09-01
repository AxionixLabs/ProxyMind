# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from frontends.cli import bootstrap
from mind_app.controller import Mind
from frontends.interaction.contracts import PromptContext
from infrastructure.mcp import external_runtime as external
from frontends.helix import runtime as service_runtime
from mind_app.runtime.mcp import tool_runtime
from infrastructure.mcp.external_runtime import ExternalMcpRuntime
from agent.harness.mcp.owner import McpRuntimeOwner
from mind_app.runtime.mcp.tool_runtime import CompositeToolRuntime
from frontends.tui.core.render import fragments_text
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.features import helix
from frontends.tui.session import loop
from agent.domain.policies import preset_permissions
from agent.application import TurnApplication
from agent.harness.sessions.owner import SessionRuntimeOwner


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

    async def monitor(_runtime, _mind):
        await asyncio.Event().wait()

    class ApplicationStub(object):
        def emit(self, _view):
            return None

    class MindStub(object):
        task_event = asyncio.Event()
        subscription = SimpleNamespace(current=None)
        permissions = preset_permissions("auto")
        pref = SimpleNamespace(to_config=lambda: {
            "primary": {"model": "test-model"},
        })
        frontend = SimpleNamespace(
            runtime=runtime,
            interaction=runtime,
            application=ApplicationStub(),
        )

        async def fresh_pref_config(self):
            refresh_started.set()
            await release_refresh.wait()
            return self.pref.to_config()

    monkeypatch.setattr(loop, "monitor_exec_status", monitor)

    run_task = asyncio.create_task(loop.run_tui_loop(
        MindStub(),
        turn_runner=AsyncMock(),
    ))
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

    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task
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
        _mind,
        tool_profile="app",
        *,
        download_confirmed=False,
    ):
        calls.append(("helix", tool_profile, download_confirmed))
        return True

    class MindStub(object):
        frontend = SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        )

        def __init__(self) -> None:
            runtime = SimpleNamespace(last_start_snapshot={
                "done": True,
                "items": [
                    {"name": "docs", "state": "ready", "tools": 2},
                ],
            })
            self.external_mcp = SimpleNamespace(
                current=runtime,
                start=self._start_external_mcp,
            )

        def is_service_mcp_linked(self):
            return False

        async def _start_external_mcp(
            self,
            *,
            defer_activity_stop=False,
        ):
            assert defer_activity_stop
            calls.append(("external", True))

        async def stop_anim(self, _kind=None, *, settle=True):
            assert not settle

        async def await_cleanup(self, awaitable):
            await awaitable

    monkeypatch.setattr(helix, "prepare_tui_service_runtime", prepare_helix)

    mind = MindStub()
    await bootstrap.start_tui_external_mcp(mind)
    await bootstrap.start_tui_service_runtime(mind, **startup_options)

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
    mind = SimpleNamespace(
        service_runtime=SimpleNamespace(
            manager=SimpleNamespace(ensure_running=AsyncMock()),
            start_keepalive=Mock(),
        ),
        start_inbuild_startup_anim=AsyncMock(),
        stop_anim=AsyncMock(),
        await_cleanup=lambda awaitable: awaitable,
    )

    await service_runtime.start_service_runtime(mind)

    mind.stop_anim.assert_awaited_once_with("inbuild", settle=False)
    mind.service_runtime.start_keepalive.assert_called_once_with()


@pytest.mark.anyio
async def test_external_mcp_concurrent_start_waits_for_first_start(
    monkeypatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    start_called = Mock()

    class ExternalGroup(object):
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

    class MindStub(object):
        src_opera_place = ""
        stop_calls = []
        config_session = SimpleNamespace(load=lambda: {
            "mcp_servers": {
                "docs": {"command": "docs-server"},
            },
        })

        async def start_external_mcp_anim(self, _snapshot):
            return None

        async def stop_anim(self, _kind=None, *, settle=True):
            self.stop_calls.append((_kind, settle))
            return None

        async def await_cleanup(self, awaitable):
            await awaitable

    monkeypatch.setattr(external, "ExternalMcpGroup", ExternalGroup)

    mind = MindStub()
    runtime = ExternalMcpRuntime(mind)
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
    assert mind.stop_calls == [("external_mcp", False)]

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

    class MindStub(object):
        src_opera_place = ""
        config_session = SimpleNamespace(load=lambda: {
            "mcp_servers": {
                "docs": {"command": "docs-server"},
            },
        })

        async def start_external_mcp_anim(self, _snapshot):
            return None

        async def stop_anim(self, _kind=None, *, settle=True):
            _ = settle
            return None

        async def await_cleanup(self, awaitable):
            await awaitable

    monkeypatch.setattr(
        external,
        "ExternalMcpGroup",
        ExternalGroup,
    )

    runtime = ExternalMcpRuntime(MindStub())
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
    mind = SimpleNamespace(await_cleanup=Mind.await_cleanup)
    owner = McpRuntimeOwner(runtime_factory=lambda: factory(mind))

    await owner.start(include_disabled=True)
    await owner.start()
    await owner.restart(defer_activity_stop=True)

    assert owner.current is runtime
    factory.assert_called_once_with(mind)
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

    class MindStub(object):
        @staticmethod
        async def await_cleanup(awaitable) -> None:
            task = asyncio.create_task(awaitable)
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

    runtime = ExternalMcpRuntime(MindStub())
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
    mind = SimpleNamespace(
        external_mcp=SimpleNamespace(current=initial_runtime),
        client_tools=object(),
        is_service_mcp_linked=lambda: False,
    )

    async def build_context(_service, external_group, *, client_registry):
        _ = client_registry
        captured_groups.append(external_group)
        build_started.set()
        await release_build.wait()
        return SimpleNamespace(session=object(), tools=[])

    async def user_flow(_session, _tools):
        return None

    monkeypatch.setattr(tool_runtime, "build_tool_context", build_context)

    runtime = CompositeToolRuntime(mind)
    turn = asyncio.create_task(runtime.with_session({}, user_flow))
    await build_started.wait()

    initial_runtime.group = SimpleNamespace(tools={"late": object()})
    release_build.set()
    await turn

    assert captured_groups == [None]
