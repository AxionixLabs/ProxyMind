# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace

import pytest

from mind_app.tui.features import conversation


@pytest.mark.anyio
async def test_compact_empty_stream_finishes_failed_activity_status(monkeypatch) -> None:
    snapshots = []

    async def empty_stream(_payload):
        if False:
            yield {}

    class ConversationStub(object):
        def snapshot(self):
            return {"cid": "cid", "sid": "sid"}

    class MindStub(object):
        animate = True
        conversation = ConversationStub()

        def __init__(self):
            self.views = []
            self.frontend = SimpleNamespace(
                application=SimpleNamespace(emit=self.views.append),
            )

        async def start_compact_anim(self, snapshot):
            snapshots.append(snapshot)

        async def stop_anim(self, kind=None, *, settle=True):
            _ = settle
            snapshots.append((kind, snapshots[0]()))

        async def await_cleanup(self, awaitable):
            await awaitable

    monkeypatch.setattr(conversation, "stream_compact_events", empty_stream)

    mind = MindStub()
    status = await conversation.compact_current_conversation(
        mind,
        run_mode="chat",
        pref_config={},
    )
    await conversation.finish_compact_activity(mind)
    conversation.render_compact_result(mind, status)

    kind, final = snapshots[-1]
    assert kind == "compact"
    assert final["done"] is True
    assert final["summary"] == "Context compaction failed. Please try again."
    assert final["detail_limit"] == 0
    status = next(view for view in mind.views if view.type == "tui.compact.status")
    assert status.renderable.plain_text == (
        "■ Context compaction failed. Please try again."
    )


@pytest.mark.anyio
async def test_compact_success_is_committed_to_tui(monkeypatch) -> None:
    async def completed_stream(_payload):
        yield {
            "type": "conversation.compact",
            "message": "Context compacted.",
            "before_items": 18,
            "after_items": 6,
        }

    class MindStub(object):
        animate = False
        conversation = SimpleNamespace(
            snapshot=lambda: {"cid": "cid", "sid": "sid"},
        )

        def __init__(self):
            self.views = []
            self.frontend = SimpleNamespace(
                application=SimpleNamespace(emit=self.views.append),
            )

    monkeypatch.setattr(conversation, "stream_compact_events", completed_stream)

    mind = MindStub()
    result = await conversation.compact_current_conversation(
        mind,
        run_mode="chat",
        pref_config={},
    )
    conversation.render_compact_result(mind, result)

    status = next(view for view in mind.views if view.type == "tui.compact.status")
    assert status.renderable.plain_text == (
        "■ Context compacted. · 18 -> 6 items"
    )


@pytest.mark.anyio
async def test_compact_cancellation_clears_animation_without_failure(
    monkeypatch,
) -> None:
    started = asyncio.Event()

    async def pending_stream(_payload):
        started.set()
        await asyncio.Future()
        if False:
            yield {}

    class MindStub(object):
        animate = True
        conversation = SimpleNamespace(
            snapshot=lambda: {"cid": "cid", "sid": "sid"},
        )

        def __init__(self):
            self.views = []
            self.stopped = []
            self.frontend = SimpleNamespace(
                application=SimpleNamespace(emit=self.views.append),
            )

        async def start_compact_anim(self, _snapshot):
            return None

        async def stop_anim(self, kind=None, *, settle=True):
            self.stopped.append((kind, settle))

        async def await_cleanup(self, awaitable):
            await awaitable

    monkeypatch.setattr(conversation, "stream_compact_events", pending_stream)

    mind = MindStub()
    task = asyncio.create_task(conversation.compact_current_conversation(
        mind,
        run_mode="chat",
        pref_config={},
    ))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    await conversation.finish_compact_activity(mind)
    conversation.render_compact_interrupted(mind)

    assert mind.stopped == [("compact", False)]
    assert any(view.type == "tui.compact.interrupted" for view in mind.views)
    assert not any(view.type == "tui.compact.status" for view in mind.views)


if __name__ == '__main__':
    pass
