# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace

import pytest

from mind_app.cli import entry
from mind_app.controller import Mind
from mind_app.interaction.contracts import PromptContext
from mind_app.runtime.mcp import external
from mind_app.runtime.mcp import tool_runtime
from mind_app.runtime.mcp.external import ExternalMcpRuntime
from mind_app.runtime.mcp.tool_runtime import CompositeToolRuntime
from mind_app.tui.core.render import fragments_text
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.features import helix
from mind_app.tui.session import loop


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
        mode="chat",
        model="test-model",
    )))
    runtime.screen.input.buffer.text = "what project is this?"
    runtime.submissions.accept_input(runtime.screen.input.buffer)

    assert await asyncio.wait_for(read_task, timeout=0.2) == "what project is this?"
    assert not infrastructure_task.done()
    assert fragments_text(runtime.document.fragments(width=80)) == (
        "> what project is this?"
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

    run_task = asyncio.create_task(loop.run_tui_loop(MindStub()))
    await refresh_started.wait()

    runtime.screen.input.buffer.text = "show this immediately"
    runtime.submissions.accept_input(runtime.screen.input.buffer)
    for _ in range(20):
        if runtime.document.blocks:
            break
        await asyncio.sleep(0)

    assert fragments_text(runtime.document.fragments(width=80)) == (
        "> show this immediately"
    )
    assert not run_task.done()

    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task
    await runtime.close()


@pytest.mark.anyio
async def test_tui_starts_external_mcp_before_helix_background(
    monkeypatch,
) -> None:
    calls = []
    views = []

    async def prepare_helix(_mind, *, download_confirmed=False):
        calls.append(("helix", download_confirmed))
        return True

    class MindStub(object):
        external_mcp = SimpleNamespace(last_start_snapshot={
            "done": True,
            "items": [
                {"name": "docs", "state": "ready", "tools": 2},
            ],
        })
        frontend = SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        )

        async def start_external_mcp_runtime(self):
            calls.append(("external", True))

    monkeypatch.setattr(helix, "prepare_tui_service_runtime", prepare_helix)

    mind = MindStub()
    await entry._start_tui_external_mcp(mind)
    await entry._start_tui_service_runtime(mind)

    assert calls == [("external", True), ("helix", True)]
    status = next(
        view for view in views
        if view.type == "tui.external_mcp.status"
    )
    assert status.renderable.plain_text == (
        "■ External MCP ready · 1/1 servers · 2 tools"
    )


@pytest.mark.anyio
async def test_service_runtime_startup_reuses_first_ready_result() -> None:
    mind = Mind.__new__(Mind)
    mind._service_start_lock = asyncio.Lock()
    mind._service_start_task = None
    mind.service_mcp_linked = False
    operation_calls = 0

    async def operation() -> bool:
        nonlocal operation_calls
        operation_calls += 1
        await asyncio.sleep(0)
        mind.service_mcp_linked = True
        return True

    results = await asyncio.gather(
        mind.run_service_runtime_startup(operation),
        mind.run_service_runtime_startup(operation),
    )

    assert results == [True, True]
    assert operation_calls == 1

    mind.service_mcp_linked = False
    assert await mind.run_service_runtime_startup(operation)
    assert operation_calls == 2


@pytest.mark.anyio
async def test_external_mcp_concurrent_start_waits_for_first_start(
    monkeypatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    enter_count = 0

    class ExternalContext(object):
        async def __aenter__(self):
            nonlocal enter_count
            enter_count += 1
            entered.set()
            await release.wait()
            return SimpleNamespace(tools={})

        async def __aexit__(self, _type, _value, _traceback):
            return None

    class MindStub(object):
        src_opera_place = ""
        stop_calls = []

        async def start_external_mcp_anim(self, _snapshot):
            return None

        async def stop_anim(self, _kind=None, *, settle=True):
            self.stop_calls.append((_kind, settle))
            return None

        async def await_cleanup(self, awaitable):
            await awaitable

    def open_group(servers, status=None):
        if status is not None:
            status.mark_ready(servers[0], "docs", 2)
            status.finish()
        return ExternalContext()

    monkeypatch.setattr(
        external,
        "load_mcp_servers_file",
        lambda _root: [{"name": "docs", "enabled": True}],
    )
    monkeypatch.setattr(external, "open_optional_external_mcp_group", open_group)

    mind = MindStub()
    runtime = ExternalMcpRuntime(mind)
    first = asyncio.create_task(runtime.start())
    second = asyncio.create_task(runtime.start())
    await entered.wait()

    assert not second.done()

    release.set()
    await asyncio.gather(first, second)

    assert enter_count == 1
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
    enter_count = 0

    class ExternalContext(object):
        async def __aenter__(self):
            nonlocal enter_count
            enter_count += 1
            return None

        async def __aexit__(self, _type, _value, _traceback):
            return None

    class MindStub(object):
        src_opera_place = ""

        async def start_external_mcp_anim(self, _snapshot):
            return None

        async def stop_anim(self, _kind=None, *, settle=True):
            _ = settle
            return None

        async def await_cleanup(self, awaitable):
            await awaitable

    monkeypatch.setattr(
        external,
        "load_mcp_servers_file",
        lambda _root: [{"name": "docs", "enabled": True}],
    )
    monkeypatch.setattr(
        external,
        "open_optional_external_mcp_group",
        lambda _servers, status=None: ExternalContext(),
    )

    runtime = ExternalMcpRuntime(MindStub())
    await runtime.start()
    await runtime.start()

    assert enter_count == 2
    assert not runtime.started
    assert runtime.group is None


@pytest.mark.anyio
async def test_external_mcp_stop_finishes_cleanup_when_cancelled() -> None:
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    class ExternalContext(object):
        async def __aexit__(self, _type, _value, _traceback):
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
    runtime._context = ExternalContext()
    runtime._group = object()
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
async def test_model_turn_keeps_external_tool_snapshot_from_session_start(
    monkeypatch,
) -> None:
    build_started = asyncio.Event()
    release_build = asyncio.Event()
    captured_groups = []
    initial_runtime = SimpleNamespace(group=None)
    mind = SimpleNamespace(
        external_mcp=initial_runtime,
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
