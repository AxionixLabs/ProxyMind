# -*- coding: utf-8 -*-

from types import SimpleNamespace

import httpx
import pytest

from mind_app.tui.features import conversation
from mind_nova.requests import fork as fork_request
from mind_nova.requests.fork import ConversationForkRequestError


class ForkMindStub(object):
    """提供会话分支功能测试所需的最小控制接口。"""

    animate = True

    def __init__(self) -> None:
        self.views = []
        self.started = []
        self.cleared = []
        self.bound = []
        self.stopped = None
        self.conversation = SimpleNamespace(
            snapshot=lambda: {
                "cid": "cid_source_12345678",
                "sid": "sid_source_1_abcdef",
            },
        )
        self.frontend = SimpleNamespace(
            application=SimpleNamespace(emit=self.views.append),
        )

    def prepare_conversation_fork(self, mode, cid, sid):
        assert (mode, cid, sid) == (
            "chat",
            "cid_source_12345678",
            "sid_source_1_abcdef",
        )
        return "fork_request_0001"

    def bind_conversation(self, cid, sid, *, source):
        self.bound.append((cid, sid, source))
        return {"cid": cid, "sid": sid}

    def clear_conversation_fork(self, mode, cid, sid, request_id):
        self.cleared.append((mode, cid, sid, request_id))

    async def start_compact_anim(self, snapshot):
        self.started.append(snapshot)

    async def stop_anim(self, kind=None, *, settle=True):
        self.stopped = (kind, settle)


@pytest.mark.anyio
async def test_fork_switches_only_after_remote_copy_succeeds(monkeypatch) -> None:
    mind = ForkMindStub()

    async def request_fork(**kwargs):
        assert kwargs["request_id"] == "fork_request_0001"
        assert mind.bound == []
        return {
            "request_id": "fork_request_0001",
            "mode": "chat",
            "source_cid": "cid_source_12345678",
            "source_sid": "sid_source_1_abcdef",
            "cid": "cid_target_87654321",
            "sid": "sid_target_2_fedcba",
            "copied_items": 24,
        }

    monkeypatch.setattr(conversation, "request_conversation_fork", request_fork)

    status = await conversation.fork_current_conversation(mind, run_mode="chat")
    await conversation.finish_fork_activity(mind)
    conversation.render_fork_result(mind, status)

    assert mind.bound == [
        ("cid_target_87654321", "sid_target_2_fedcba", "tui")
    ]
    assert mind.cleared == [
        (
            "chat",
            "cid_source_12345678",
            "sid_source_1_abcdef",
            "fork_request_0001",
        )
    ]
    assert mind.started[0]()["items"][0]["name"] == "Fork"
    assert mind.stopped == ("compact", False)
    result = next(view for view in mind.views if view.type == "tui.fork.status")
    assert result.renderable.plain_text == "■ Conversation forked. · 24 items"


@pytest.mark.anyio
async def test_retryable_fork_failure_keeps_pending_request(monkeypatch) -> None:
    mind = ForkMindStub()
    mind.animate = False

    async def request_fork(**_kwargs):
        raise ConversationForkRequestError(
            "Conversation is busy. Try /fork again after the current turn finishes.",
            status_code=409,
            code="source_busy",
            retryable=True,
        )

    monkeypatch.setattr(conversation, "request_conversation_fork", request_fork)

    status = await conversation.fork_current_conversation(mind, run_mode="chat")
    conversation.render_fork_result(mind, status)

    assert mind.bound == []
    assert mind.cleared == []
    result = next(view for view in mind.views if view.type == "tui.fork.status")
    assert result.renderable.plain_text == (
        "■ Conversation is busy. "
        "Try /fork again after the current turn finishes."
    )


@pytest.mark.anyio
async def test_fork_request_parses_source_busy_error(monkeypatch) -> None:
    response = httpx.Response(
        409,
        json={
            "error": "FATAL",
            "details": {
                "code": "source_busy",
                "message": "source conversation is busy",
            },
        },
    )

    class ClientStub(object):
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return response

    monkeypatch.setattr(fork_request.httpx, "AsyncClient", ClientStub)

    with pytest.raises(ConversationForkRequestError) as raised:
        await fork_request.request_conversation_fork(
            mode="chat",
            cid="cid_source_12345678",
            sid="sid_source_1_abcdef",
            request_id="fork_request_0001",
        )

    assert raised.value.code == "source_busy"
    assert raised.value.retryable is True


if __name__ == '__main__':
    pass
