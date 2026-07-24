# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from engine.errors import ApplicationError
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.features import helix
from mind_app.tui.prompting.commands import (
    SlashCommandCompleter,
    command_names,
    is_unrecognized_slash_command,
    parameterized_command_texts,
    resolve_slash_command,
    stream_command_label,
    stream_command_policy,
)
from mind_app.tui.session.dispatch import (
    DispatchAction,
    MODE_BY_COMMAND,
    TuiCommandDispatcher,
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


@pytest.mark.parametrize(
    "value",
    ["/permissions", "/MODEL gpt-test", "/mcp start", "/q"],
)
def test_registered_slash_command_inputs_are_resolved(value) -> None:
    assert resolve_slash_command(value) is not None
    assert not is_unrecognized_slash_command(value)


@pytest.mark.parametrize(
    "value",
    ["/今天天气", "/compact later", "/mcp unknown"],
)
def test_unknown_or_invalid_slash_command_inputs_are_rejected(value) -> None:
    assert resolve_slash_command(value) is None
    assert is_unrecognized_slash_command(value)


def test_root_slash_only_opens_completion() -> None:
    assert resolve_slash_command("/") is None
    assert not is_unrecognized_slash_command("/")


@pytest.mark.anyio
async def test_dispatcher_never_sends_unknown_slash_command_to_model() -> None:
    views = []
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )
    dispatcher = TuiCommandDispatcher(
        mind,
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
    )

    action = await dispatcher.dispatch("/今天天气")

    assert action is DispatchAction.HANDLED
    assert "".join(
        text for _style, text in views[-1].renderable.fragments
    ) == (
        "Unrecognized command '/今天天气'. "
        'Type "/" for a list of supported commands.'
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("result", "error", "expected"),
    [
        (True, None, "■ Helix MCP ready"),
        (None, ApplicationError("startup timeout"), "■ Helix MCP failed\n└ startup timeout"),
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

    try:
        linked = await helix.link_helix_runtime(mind)
    except (ApplicationError, Exception) as captured:
        helix.render_helix_link_failure(mind, captured)
    else:
        helix.render_helix_link_result(mind, linked)

    prepare.assert_awaited_once_with(mind, download_confirmed=False)
    status = next(view for view in views if view.type == "tui.helix.status")
    assert status.renderable.plain_text == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, "■ Helix MCP stopped"),
        (
            ApplicationError("port cleanup failed"),
            "■ Helix MCP stop failed\n└ port cleanup failed",
        ),
    ],
)
async def test_helix_stop_commits_one_final_status(error, expected) -> None:
    views = []
    mind = SimpleNamespace(
        stop_service_runtime=AsyncMock(side_effect=error),
        frontend=SimpleNamespace(
            runtime=TuiRuntime(),
            application=SimpleNamespace(emit=views.append),
        ),
    )

    try:
        result = await helix.stop_helix_runtime(mind)
    except (ApplicationError, Exception) as captured:
        await mind.frontend.runtime.end_activity_status(
            "operation",
            settle=False,
        )
        helix.render_helix_stop_failure(mind, captured)
    else:
        await mind.frontend.runtime.end_activity_status(
            "operation",
            settle=False,
        )
        helix.render_helix_stop_result(mind, result)

    statuses = [view for view in views if view.type == "tui.helix.status"]
    assert len(statuses) == 1
    assert statuses[0].renderable.plain_text == expected


def _completions(text: str):
    return list(SlashCommandCompleter().get_completions(
        Document(text=text, cursor_position=len(text)),
        CompleteEvent(completion_requested=True),
    ))
