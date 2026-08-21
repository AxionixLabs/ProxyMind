# -*- coding: utf-8 -*-

import asyncio
import contextvars

import pytest

from mind_app.tui.contracts.text import FragmentBlock
from mind_app.tui.runtime.background import (
    BackgroundTaskManager,
    DeferredBlock,
    DeferredBlockBuffer,
)
from mind_app.tui.runtime.state import (
    ActivityHandoffState,
    CommandLayoutState,
    ProcessCompletionStore,
)


def _block(text: str) -> FragmentBlock:
    return FragmentBlock((('', text),))


def test_process_completion_store_isolates_snapshots_and_preserves_latest() -> None:
    store = ProcessCompletionStore()
    source = {
        "session_id": "session-a",
        "items": [{"name": "docs"}],
    }

    assert store.retain(source, label="first")
    source["items"].append({"name": "mailbox"})
    assert store.snapshots() == ({
        "session_id": "session-a",
        "items": [{"name": "docs"}],
    },)

    assert store.retain({"session_id": "session-b"}, label="second")
    assert store.latest_label("running") == "second"

    returned = store.snapshots()
    returned[0]["items"].clear()
    assert store.snapshots()[0]["items"] == [{"name": "docs"}]

    assert store.acknowledge("session-b")
    assert store.latest_label("running") == "first"


def test_deferred_block_buffer_transfers_a_single_batch() -> None:
    buffer = DeferredBlockBuffer()
    item = DeferredBlock(
        block=_block("display"),
        transcript_block=_block("transcript"),
    )

    buffer.append(item)

    assert tuple(buffer) == (item,)
    assert buffer.drain() == (item,)
    assert len(buffer) == 0
    assert buffer.drain() == ()


def test_command_layout_state_consumes_only_the_current_context() -> None:
    state = CommandLayoutState()
    state.begin()

    assert state.pending
    assert state.consume()
    assert not state.pending
    assert not state.consume()

    state.begin()
    child_context = contextvars.copy_context()
    child_context.run(state.cancel)
    assert state.pending

    state.cancel()
    assert not state.pending


def test_activity_handoff_state_freezes_deferred_leases_once() -> None:
    state = ActivityHandoffState()
    lease = object()
    frozen: list[object] = []

    with state.bind(lease, deferred=False) as handoff:  # type: ignore[arg-type]
        assert state.consume(
            deferred=True,
            freeze=frozen.append,
        ) is lease
        assert frozen == [lease]
        assert handoff.consumed
        assert state.consume(deferred=True, freeze=frozen.append) is None


@pytest.mark.anyio
async def test_background_task_manager_reports_failures_and_closes_tasks() -> None:
    errors: list[BaseException] = []
    manager = BackgroundTaskManager(errors.append)
    expected = RuntimeError("background state probe")

    async def fail() -> None:
        raise expected

    task = manager.start(fail(), name="state probe")
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert errors == [expected]
    assert not manager

    release = asyncio.Event()

    async def wait() -> None:
        await release.wait()

    manager.start_session("session-a", wait())
    await manager.close()
    assert not manager
