# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from engine.errors import MindError
from mind_app.tui.features import helix
from mind_app.tui.prompting.commands import (
    SlashCommandCompleter,
    command_names,
    parameterized_command_texts,
    stream_command_label,
    stream_command_policy,
)
from mind_app.tui.session.dispatch import (
    MODE_BY_COMMAND,
)


def test_root_command_completion_order_is_stable() -> None:
    completions = _completions("/")

    assert [item.display_text for item in completions] == [
        "/chat",
        "/fast",
        "/xtra",
        "/new",
        "/resume",
        "/permissions",
        "/model",
        "/effort",
        "/preferences",
        "/compact",
        "/tools",
        "/diff",
        "/copy",
        "/ps",
        "/mcp",
        "/helix-link",
        "/helix-unlink",
        "/helix-home",
        "/helix-stop",
        "/skills",
        "/shutdown",
        "/quit",
    ]
    assert next(item for item in completions if item.display_text == "/skills").text == "$"


def test_helix_prefix_keeps_command_order() -> None:
    completions = _completions("/h")

    assert [item.display_text for item in completions] == [
        "/helix-link",
        "/helix-unlink",
        "/helix-home",
        "/helix-stop",
    ]


def test_command_catalog_preserves_dispatch_and_input_policies() -> None:
    assert command_names("quit") == frozenset({"/quit", "/q", "quit", "exit"})
    assert parameterized_command_texts() == ("/model ",)
    assert stream_command_policy("/helix-link") == "background_barrier"
    assert stream_command_policy("/mcp start") == "background_barrier"
    assert stream_command_policy("/mcp force") == "background_barrier"
    assert stream_command_policy("/compact") == "reject"
    assert stream_command_policy("/mcp restart") == "reject"
    assert stream_command_policy("! rg foo") == "reject"
    assert stream_command_policy("/quit") == "interrupt"
    assert stream_command_policy("hello") is None
    assert stream_command_policy("$review") is None
    assert stream_command_label("/mcp restart now") == "/mcp restart"
    assert MODE_BY_COMMAND == {
        "/chat": "chat",
        "/fast": "fast",
        "/xtra": "xtra",
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("result", "error", "expected"),
    [
        (True, None, "■ Helix MCP ready"),
        (None, MindError("startup timeout"), "■ Helix MCP failed\n└ startup timeout"),
        (
            None,
            RuntimeError("process exited"),
            "■ Helix MCP failed\n└ RuntimeError: process exited",
        ),
    ],
)
async def test_helix_link_result_is_committed_to_tui(
    monkeypatch,
    result,
    error,
    expected,
) -> None:
    views = []
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )
    prepare = (
        AsyncMock(side_effect=error)
        if error
        else AsyncMock(return_value=result)
    )
    monkeypatch.setattr(helix, "prepare_tui_service_runtime", prepare)

    await helix.link_helix_runtime(mind)

    status = next(view for view in views if view.type == "tui.helix.status")
    assert status.renderable.plain_text == expected


def _completions(text: str):
    return list(SlashCommandCompleter().get_completions(
        Document(text=text, cursor_position=len(text)),
        CompleteEvent(completion_requested=True),
    ))
