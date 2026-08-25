# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from mind_app.tui.features import conversation
from mind_nova.requests import fork as fork_request
from mind_nova.requests.fork import (
    ConversationForkRequestError,
    ResubmittablePrompt,
)


class ForkMindStub(object):
    """提供会话分支功能测试所需的最小控制接口。"""

    animate = True

    def __init__(self) -> None:
        self.views = []
        self.started = []
        self.cleared = []
        self.bound = []
        self.resets = []
        self.stopped = None
        self.conversation = SimpleNamespace(
            fork_source_available=True,
            snapshot=lambda: {
                "cid": "cid_source_12345678",
                "sid": "sid_source_1_abcdef",
            },
        )
        self.frontend = SimpleNamespace(
            application=SimpleNamespace(emit=self.views.append),
        )

    def prepare_conversation_fork(
        self,
        cid,
        sid,
        before_turn_id="",
    ):
        assert (cid, sid) == (
            "cid_source_12345678",
            "sid_source_1_abcdef",
        )
        assert before_turn_id in {"", "turn_selected"}
        return "fork_request_0001"

    async def bind_conversation(self, cid, sid, *, source):
        self.bound.append((cid, sid, source))
        return {"cid": cid, "sid": sid}

    async def reset_conversation(self, *, reason, source):
        self.resets.append((reason, source))
        return {
            "cid": "cid_target_87654321",
            "sid": "sid_target_2_fedcba",
        }

    def clear_conversation_fork(
        self,
        cid,
        sid,
        request_id,
        before_turn_id="",
    ):
        self.cleared.append((
            cid,
            sid,
            request_id,
            before_turn_id,
        ))

    async def start_compact_anim(self, snapshot):
        self.started.append(snapshot)

    async def stop_anim(self, kind=None, *, settle=True):
        self.stopped = (kind, settle)


def test_fork_payload_requires_prompt_source_matching_boundary() -> None:
    common = {
        "cid": "cid_source_12345678",
        "sid": "sid_source_1_abcdef",
        "request_id": "fork_request_0001",
    }

    with pytest.raises(ValueError, match="unbounded fork"):
        fork_request.build_fork_payload(**common, prompt_source="server")
    with pytest.raises(ValueError, match="bounded fork"):
        fork_request.build_fork_payload(
            **common,
            prompt_source="none",
            before_turn_id="turn_selected",
        )


@pytest.mark.anyio
async def test_fork_switches_only_after_remote_copy_succeeds(monkeypatch) -> None:
    mind = ForkMindStub()

    async def request_fork(**kwargs):
        assert kwargs["request_id"] == "fork_request_0001"
        assert kwargs["prompt_source"] == "none"
        assert mind.bound == []
        return {
            "request_id": "fork_request_0001",
            "source_cid": "cid_source_12345678",
            "source_sid": "sid_source_1_abcdef",
            "cid": "cid_target_87654321",
            "sid": "sid_target_2_fedcba",
            "copied_items": 24,
        }

    monkeypatch.setattr(conversation, "request_conversation_fork", request_fork)

    status = await conversation.fork_current_conversation(mind)
    conversation.render_fork_result(mind, status)

    assert mind.bound == [
        ("cid_target_87654321", "sid_target_2_fedcba", "tui")
    ]
    assert mind.cleared == [
        (
            "cid_source_12345678",
            "sid_source_1_abcdef",
            "fork_request_0001",
            "",
        )
    ]
    assert mind.started[0]()["items"][0]["name"] == "Fork"
    assert mind.stopped is None
    result = next(view for view in mind.views if view.type == "tui.fork.status")
    assert result.renderable.plain_text == (
        "■ Conversation forked. · 24 items"
    )


@pytest.mark.anyio
async def test_empty_conversation_starts_new_session_without_remote_fork(
    monkeypatch,
) -> None:
    mind = ForkMindStub()
    mind.animate = False
    mind.conversation.fork_source_available = False
    request_fork = AsyncMock(
        side_effect=AssertionError("empty conversation must not call /fork")
    )

    monkeypatch.setattr(conversation, "request_conversation_fork", request_fork)

    status = await conversation.fork_current_conversation(mind)

    assert status.succeeded
    assert status.source_session == (
        "cid_source_12345678",
        "sid_source_1_abcdef",
    )
    assert status.target_session == (
        "cid_target_87654321",
        "sid_target_2_fedcba",
    )
    assert status.snapshot()["summary"] == "New conversation started."
    assert mind.resets == [("command:/fork-empty", "tui:fork-empty")]
    assert mind.cleared == []
    assert mind.started == []
    request_fork.assert_not_awaited()


@pytest.mark.anyio
async def test_source_missing_response_recovers_as_new_empty_session(
    monkeypatch,
) -> None:
    mind = ForkMindStub()
    mind.animate = False

    async def request_fork(**_kwargs):
        raise ConversationForkRequestError(
            "conversation history is empty",
            status_code=404,
            code="source_missing",
        )

    monkeypatch.setattr(conversation, "request_conversation_fork", request_fork)

    status = await conversation.fork_current_conversation(mind)

    assert status.succeeded
    assert status.snapshot()["summary"] == "New conversation started."
    assert mind.cleared == [(
        "cid_source_12345678",
        "sid_source_1_abcdef",
        "fork_request_0001",
        "",
    )]
    assert mind.resets == [("command:/fork-empty", "tui:fork-empty")]


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

    status = await conversation.fork_current_conversation(mind)
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
            cid="cid_source_12345678",
            sid="sid_source_1_abcdef",
            request_id="fork_request_0001",
            prompt_source="none",
        )

    assert raised.value.code == "source_busy"
    assert raised.value.retryable is True


@pytest.mark.anyio
async def test_fork_request_parses_empty_history_error(monkeypatch) -> None:
    response = httpx.Response(
        404,
        json={"detail": "conversation history is empty"},
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
            cid="cid_source_12345678",
            sid="sid_source_1_abcdef",
            request_id="fork_request_0001",
            prompt_source="none",
        )

    assert raised.value.status_code == 404
    assert raised.value.code == "source_missing"
    assert raised.value.message == "conversation history is empty"
    assert raised.value.retryable is False


@pytest.mark.anyio
async def test_fork_request_validates_bounded_zero_item_response(monkeypatch) -> None:
    response = httpx.Response(
        200,
        json={
            "ok": True,
            "data": {
                "request_id": "fork_request_0001",
                "source_cid": "cid_source_12345678",
                "source_sid": "sid_source_1_abcdef",
                "before_turn_id": "turn_selected",
                "prompt_source": "server",
                "cid": "cid_target_87654321",
                "sid": "sid_target_2_fedcba",
                "copied_turns": 0,
                "copied_items": 0,
                "prompt": {
                    "message": "inspect this",
                    "attachments": [{
                        "kind": "image",
                        "image_url": "https://example.test/image.png",
                    }],
                    "extras": {
                        "selection": {"x": 10, "y": 20},
                    },
                },
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

        async def post(self, *_args, **kwargs):
            assert kwargs["json"]["before_turn_id"] == "turn_selected"
            return response

    monkeypatch.setattr(fork_request.httpx, "AsyncClient", ClientStub)

    result = await fork_request.request_conversation_fork(
        cid="cid_source_12345678",
        sid="sid_source_1_abcdef",
        request_id="fork_request_0001",
        prompt_source="server",
        before_turn_id="turn_selected",
    )

    assert result["copied_turns"] == 0
    assert result["copied_items"] == 0
    assert result["prompt"] == ResubmittablePrompt(
        message="inspect this",
        attachments=({
            "kind": "image",
            "image_url": "https://example.test/image.png",
        },),
        extras={"selection": {"x": 10, "y": 20}},
    )


@pytest.mark.anyio
async def test_fork_request_defaults_empty_prompt_fields(monkeypatch) -> None:
    response = httpx.Response(
        200,
        json={
            "ok": True,
            "data": {
                "request_id": "fork_request_0001",
                "source_cid": "cid_source_12345678",
                "source_sid": "sid_source_1_abcdef",
                "before_turn_id": "turn_selected",
                "prompt_source": "server",
                "cid": "cid_target_87654321",
                "sid": "sid_target_2_fedcba",
                "copied_turns": 0,
                "copied_items": 1,
                "prompt": {"message": "inspect this"},
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

    result = await fork_request.request_conversation_fork(
        cid="cid_source_12345678",
        sid="sid_source_1_abcdef",
        request_id="fork_request_0001",
        prompt_source="server",
        before_turn_id="turn_selected",
    )

    assert result["prompt"] == ResubmittablePrompt(
        message="inspect this",
        attachments=(),
        extras={},
    )


@pytest.mark.anyio
async def test_bounded_fork_accepts_empty_source_prefix(monkeypatch) -> None:
    mind = ForkMindStub()
    mind.animate = False

    async def request_fork(**kwargs):
        assert kwargs["before_turn_id"] == "turn_selected"
        assert kwargs["prompt_source"] == "server"
        return {
            "request_id": "fork_request_0001",
            "source_cid": "cid_source_12345678",
            "source_sid": "sid_source_1_abcdef",
            "before_turn_id": "turn_selected",
            "cid": "cid_target_87654321",
            "sid": "sid_target_2_fedcba",
            "copied_turns": 0,
            "copied_items": 0,
            "prompt": ResubmittablePrompt(
                message="inspect this",
                attachments=(),
                extras={},
            ),
        }

    monkeypatch.setattr(conversation, "request_conversation_fork", request_fork)

    status = await conversation.fork_current_conversation(
        mind,
        before_turn_id="turn_selected",
    )

    assert status.succeeded
    assert status.prompt == ResubmittablePrompt(
        message="inspect this",
        attachments=(),
        extras={},
    )
    assert mind.bound == [
        ("cid_target_87654321", "sid_target_2_fedcba", "tui")
    ]
    assert mind.cleared == [(
        "cid_source_12345678",
        "sid_source_1_abcdef",
        "fork_request_0001",
        "turn_selected",
    )]


@pytest.mark.anyio
async def test_bounded_fork_can_defer_target_binding(monkeypatch) -> None:
    mind = ForkMindStub()
    mind.animate = False

    async def request_fork(**_kwargs):
        return {
            "cid": "cid_target_87654321",
            "sid": "sid_target_2_fedcba",
            "copied_items": 1,
            "prompt": ResubmittablePrompt(
                message="inspect this",
                attachments=(),
                extras={},
            ),
        }

    monkeypatch.setattr(conversation, "request_conversation_fork", request_fork)

    status = await conversation.fork_current_conversation(
        mind,
        before_turn_id="turn_selected",
        bind_target=False,
    )

    assert status.succeeded
    assert status.source_session == (
        "cid_source_12345678",
        "sid_source_1_abcdef",
    )
    assert status.target_session == (
        "cid_target_87654321",
        "sid_target_2_fedcba",
    )
    assert mind.bound == []


@pytest.mark.anyio
async def test_bounded_fork_uses_fallback_prompt_when_remote_prompt_missing(
    monkeypatch,
) -> None:
    mind = ForkMindStub()
    mind.animate = False
    fallback = ResubmittablePrompt(
        message="local prompt",
        attachments=({"kind": "file", "file_key": "file_123"},),
        extras={"selection": {"x": 10, "y": 20}},
    )

    async def request_fork(**kwargs):
        assert kwargs["before_turn_id"] == "turn_selected"
        assert kwargs["prompt_source"] == "client"
        return {
            "cid": "cid_target_87654321",
            "sid": "sid_target_2_fedcba",
            "copied_items": 1,
            "prompt": None,
        }

    monkeypatch.setattr(conversation, "request_conversation_fork", request_fork)

    status = await conversation.fork_current_conversation(
        mind,
        before_turn_id="turn_selected",
        bind_target=False,
        fallback_prompt=fallback,
    )

    assert status.succeeded
    assert status.prompt == fallback
    assert status.target_session == (
        "cid_target_87654321",
        "sid_target_2_fedcba",
    )
    assert mind.bound == []


@pytest.mark.anyio
async def test_fork_request_allows_client_owned_prompt(
    monkeypatch,
) -> None:
    response = httpx.Response(
        200,
        json={
            "ok": True,
            "data": {
                "request_id": "fork_request_0001",
                "source_cid": "cid_source_12345678",
                "source_sid": "sid_source_1_abcdef",
                "before_turn_id": "turn_selected",
                "prompt_source": "client",
                "cid": "cid_target_87654321",
                "sid": "sid_target_2_fedcba",
                "copied_turns": 0,
                "copied_items": 1,
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

        async def post(self, *_args, **kwargs):
            assert kwargs["json"]["prompt_source"] == "client"
            return response

    monkeypatch.setattr(fork_request.httpx, "AsyncClient", ClientStub)

    result = await fork_request.request_conversation_fork(
        cid="cid_source_12345678",
        sid="sid_source_1_abcdef",
        request_id="fork_request_0001",
        prompt_source="client",
        before_turn_id="turn_selected",
    )

    assert result["prompt"] is None


@pytest.mark.anyio
async def test_bounded_fork_rejects_missing_prompt(monkeypatch) -> None:
    response = httpx.Response(
        200,
        json={
            "ok": True,
            "data": {
                "request_id": "fork_request_0001",
                "source_cid": "cid_source_12345678",
                "source_sid": "sid_source_1_abcdef",
                "before_turn_id": "turn_selected",
                "prompt_source": "server",
                "cid": "cid_target_87654321",
                "sid": "sid_target_2_fedcba",
                "copied_turns": 0,
                "copied_items": 0,
                "prompt": None,
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

    with pytest.raises(
        ConversationForkRequestError,
        match="invalid prompt",
    ):
        await fork_request.request_conversation_fork(
            cid="cid_source_12345678",
            sid="sid_source_1_abcdef",
            request_id="fork_request_0001",
            prompt_source="server",
            before_turn_id="turn_selected",
        )


if __name__ == '__main__':
    pass
