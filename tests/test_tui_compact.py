# -*- coding: utf-8 -*-

import pytest

from mind_app.tui.features import commands


@pytest.mark.anyio
async def test_compact_empty_stream_persists_failed_final_status(monkeypatch) -> None:
    snapshots = []
    starts = []

    async def empty_stream(_payload):
        if False:
            yield {}

    class ConversationStub(object):
        def snapshot(self):
            return {"cid": "cid", "sid": "sid"}

    class MindStub(object):
        animate = True
        conversation = ConversationStub()

        async def start_external_mcp_anim(self, snapshot, *, persist_final=False):
            snapshots.append(snapshot)
            starts.append(persist_final)

        async def stop_anim(self, kind=None):
            snapshots.append((kind, snapshots[0]()))

        async def await_cleanup(self, awaitable):
            await awaitable

    monkeypatch.setattr(commands, "stream_compact_events", empty_stream)

    await commands.compact_current_conversation(
        MindStub(),
        run_mode="chat",
        pref_config={},
    )

    kind, final = snapshots[-1]
    assert starts == [True]
    assert kind == "external_mcp"
    assert final["done"] is True
    assert final["summary"] == "Context compaction failed. Please try again."
    assert final["detail_limit"] == 0


if __name__ == '__main__':
    pass
