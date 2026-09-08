# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.protocol import AssistantReplySnapshot
from frontends.tui.features import conversation


class _Conversation(object):
    """保存测试使用的最近 assistant 回复。"""

    def __init__(self, reply: str) -> None:
        self.reply = reply

    def assistant_reply_snapshot(self) -> AssistantReplySnapshot | None:
        if not self.reply.strip():
            return None
        return AssistantReplySnapshot.from_source(self.reply)


def _host(reply: str) -> SimpleNamespace:
    views = []
    return SimpleNamespace(
        conversation=_Conversation(reply),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
        views=views,
    )


@pytest.mark.anyio
async def test_copy_picker_freezes_response_and_copies_selected_exact_code(
    monkeypatch,
) -> None:
    host = _host("Before\n\n```sh\necho hi  \n```")
    copied = AsyncMock()
    monkeypatch.setattr(conversation, "copy_text_to_clipboard", copied)

    class Runtime(object):
        request = None

        async def select_menu(self, request):
            self.request = request
            host.conversation.reply = "After"
            return request.options[1].value

    runtime = Runtime()

    await conversation.copy_last_assistant_reply(runtime, host)

    assert runtime.request.title == "Copy from response"
    assert [option.label for option in runtime.request.options] == [
        "Whole response",
        "sh code",
    ]
    assert runtime.request.options[1].selected_body == ("echo hi  \n",)
    copied.assert_awaited_once_with("echo hi  \n")
    assert host.conversation.reply == "After"


@pytest.mark.anyio
async def test_copy_picker_cancel_does_not_touch_clipboard(monkeypatch) -> None:
    host = _host("response")
    copied = AsyncMock()
    monkeypatch.setattr(conversation, "copy_text_to_clipboard", copied)
    runtime = SimpleNamespace(select_menu=AsyncMock(return_value=None))

    await conversation.copy_last_assistant_reply(runtime, host)

    copied.assert_not_awaited()
    assert host.views == []


@pytest.mark.anyio
async def test_ctrl_o_copies_frozen_whole_response(monkeypatch) -> None:
    host = _host("line  \r\n")
    copied = AsyncMock()
    monkeypatch.setattr(conversation, "copy_text_to_clipboard", copied)

    await conversation.copy_whole_assistant_reply(
        host,
        source=host.conversation.assistant_reply_snapshot().source,
    )

    copied.assert_awaited_once_with("line")


@pytest.mark.anyio
async def test_copy_without_response_reports_codex_message(monkeypatch) -> None:
    host = _host("  \n")
    copied = AsyncMock()
    monkeypatch.setattr(conversation, "copy_text_to_clipboard", copied)
    runtime = SimpleNamespace(select_menu=AsyncMock())

    await conversation.copy_last_assistant_reply(runtime, host)

    runtime.select_menu.assert_not_awaited()
    copied.assert_not_awaited()
    rendered = str(host.views[0].renderable)
    assert "No agent response to copy" in rendered


@pytest.mark.anyio
async def test_copy_failure_uses_codex_error_message(monkeypatch) -> None:
    host = _host("response")
    monkeypatch.setattr(
        conversation,
        "copy_text_to_clipboard",
        AsyncMock(side_effect=conversation.ClipboardError("blocked")),
    )

    await conversation.copy_whole_assistant_reply(host)

    rendered = str(host.views[0].renderable)
    assert "Copy failed: blocked" in rendered
