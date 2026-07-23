# -*- coding: utf-8 -*-

from types import SimpleNamespace

import pytest

from mind_app.tui.features import commands


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

        async def start_external_mcp_anim(self, snapshot):
            snapshots.append(snapshot)

        async def stop_anim(self, kind=None):
            snapshots.append((kind, snapshots[0]()))

        async def await_cleanup(self, awaitable):
            await awaitable

    monkeypatch.setattr(commands, "stream_compact_events", empty_stream)

    mind = MindStub()
    await commands.compact_current_conversation(
        mind,
        run_mode="chat",
        pref_config={},
    )

    kind, final = snapshots[-1]
    assert kind == "external_mcp"
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

    monkeypatch.setattr(commands, "stream_compact_events", completed_stream)

    mind = MindStub()
    await commands.compact_current_conversation(
        mind,
        run_mode="chat",
        pref_config={},
    )

    status = next(view for view in mind.views if view.type == "tui.compact.status")
    assert status.renderable.plain_text == (
        "■ Context compacted. · 18 -> 6 items"
    )


if __name__ == '__main__':
    pass
