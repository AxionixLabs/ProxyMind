# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from engine.errors import AppError
from mind_core.skills import SkillSpec
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.features import helix
from mind_app.tui.features.skills import choose_skill
from mind_app.tui.prompting.commands import (
    SlashCommandCompleter,
    canonical_command_label,
    command_names,
    is_unrecognized_slash_command,
    parameterized_command_texts,
    resolve_slash_command,
    stream_command_label,
    stream_command_policy,
    submission_uses_transient_surface,
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
        "/fork",
        "/permissions",
        "/model",
        "/effort",
        "/preferences",
        "/compact",
        "/tools",
        "/hooks",
        "/agent",
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
    assert next(
        item for item in completions
        if item.display_text == "/skills"
    ).text == "/skills"


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("/q", "/quit"),
        ("quit", "/quit"),
        ("/MCP STATUS", "/mcp status"),
        ("/model gpt-test", "/model"),
    ),
)
def test_command_labels_use_canonical_names(value: str, expected: str) -> None:
    assert canonical_command_label(value) == expected


@pytest.mark.anyio
async def test_skills_command_opens_menu_and_restores_selected_token() -> None:
    runtime = TuiRuntime()
    skill = SkillSpec(
        name="review",
        description="Review the current changes",
        source="project",
        root=Path("skills/review"),
        entry=Path("skills/review/SKILL.md"),
    )
    runtime.input_model.set_skills((skill,))
    runtime.select_menu = AsyncMock(return_value=skill)

    selected = await choose_skill(runtime)

    assert selected is skill
    assert runtime.screen.input.buffer.text == "$review "
    request = runtime.select_menu.await_args.args[0]
    assert request.title == "Skills"
    assert request.options[0].label == "review"


@pytest.mark.anyio
async def test_cancelled_skills_menu_keeps_input_empty() -> None:
    runtime = TuiRuntime()
    runtime.select_menu = AsyncMock(return_value=None)

    assert await choose_skill(runtime) is None
    assert runtime.screen.input.buffer.text == ""


def test_helix_prefix_keeps_command_order() -> None:
    completions = _completions("/h")

    assert [item.display_text for item in completions] == [
        "/hooks",
        "/helix-link",
        "/helix-unlink",
        "/helix-home",
        "/helix-stop",
    ]


def test_complete_command_remains_available_to_the_menu() -> None:
    assert [item.display_text for item in _completions("/mc")] == ["/mcp"]
    assert _completions("/mcp") == []
    assert [
        item.display_text for item in _slash_completions("/mcp")
    ] == ["/mcp"]


def test_command_matching_is_case_insensitive_and_prioritizes_exact_alias() -> None:
    assert [
        item.display_text for item in _slash_completions("/FAST")
    ] == ["/fast"]
    assert [
        item.display_text for item in _slash_completions("/q")
    ] == ["/q", "/quit"]


def test_command_matching_distinguishes_empty_and_argument_states() -> None:
    assert _slash_completions("/aaa") == ()
    assert _slash_completions("/model ") is None


@pytest.mark.parametrize(
    "value",
    [
        "/permissions",
        "/effort",
        "/resume",
        "/hooks",
        "/agent",
        "/ps",
        "/mcp",
        "/helix-link",
    ],
)
def test_bare_surface_commands_stage_their_submission(value) -> None:
    assert submission_uses_transient_surface(value)


@pytest.mark.parametrize("value", ["hello", "/mcp status", "/model gpt-test", "/q"])
def test_non_surface_inputs_commit_directly(value) -> None:
    assert not submission_uses_transient_surface(value)


def test_command_catalog_preserves_dispatch_and_input_policies() -> None:
    assert command_names("quit") == frozenset({"/quit", "/q", "quit", "exit"})
    assert parameterized_command_texts() == ("/model ",)
    assert stream_command_policy("/helix-link") == "background_barrier"
    assert stream_command_policy("/mcp start") == "background_barrier"
    assert stream_command_policy("/mcp force") == "background_barrier"
    assert stream_command_policy("/ps") == "local_snapshot"
    assert stream_command_policy("/agent") == "local_snapshot"
    assert stream_command_policy("/compact") == "reject"
    assert stream_command_policy("/hooks") == "reject"
    assert stream_command_policy("/fork") == "reject"
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
async def test_dispatcher_routes_hooks_to_the_management_surface(
    monkeypatch,
) -> None:
    from mind_app.tui.session import dispatch as dispatch_module

    runtime = SimpleNamespace()
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
    )
    manage = AsyncMock()
    monkeypatch.setattr(dispatch_module, "manage_hooks", manage)
    dispatcher = TuiCommandDispatcher(
        mind,
        runtime,
        SimpleNamespace(),
        SimpleNamespace(),
    )

    action = await dispatcher.dispatch("/hooks")

    assert action is DispatchAction.HANDLED
    manage.assert_awaited_once_with(runtime, mind)


@pytest.mark.anyio
async def test_dispatcher_routes_agent_to_the_management_surface(
    monkeypatch,
) -> None:
    from mind_app.tui.session import dispatch as dispatch_module

    runtime = SimpleNamespace()
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
    )
    manage = AsyncMock()
    monkeypatch.setattr(dispatch_module, "manage_agents", manage)
    dispatcher = TuiCommandDispatcher(
        mind,
        runtime,
        SimpleNamespace(),
        SimpleNamespace(),
    )

    action = await dispatcher.dispatch("/agent")

    assert action is DispatchAction.HANDLED
    manage.assert_awaited_once_with(runtime, mind)


@pytest.mark.anyio
async def test_dispatcher_routes_agent_stream_snapshot_without_interrupting(
    monkeypatch,
) -> None:
    from mind_app.tui.session import dispatch as dispatch_module

    called = asyncio.Event()
    runtime = TuiRuntime()
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
    )

    async def append_snapshot(received_runtime, received_mind):
        assert received_runtime is runtime
        assert received_mind is mind
        called.set()

    monkeypatch.setattr(
        dispatch_module,
        "append_agent_stream_snapshot",
        append_snapshot,
    )
    dispatcher = TuiCommandDispatcher(
        mind,
        runtime,
        SimpleNamespace(),
        SimpleNamespace(handle_stream_command=lambda *_args: False),
    )
    cancelled = []

    handled = dispatcher.handle_stream_command(
        "/agent",
        lambda: cancelled.append(True) or True,
    )
    await asyncio.wait_for(called.wait(), timeout=1)

    assert handled
    assert cancelled == []


@pytest.mark.anyio
async def test_dispatcher_routes_skills_to_the_picker(monkeypatch) -> None:
    from mind_app.tui.session import dispatch as dispatch_module

    runtime = SimpleNamespace()
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
    )
    choose = AsyncMock()
    monkeypatch.setattr(dispatch_module, "choose_skill", choose)
    dispatcher = TuiCommandDispatcher(
        mind,
        runtime,
        SimpleNamespace(),
        SimpleNamespace(),
    )

    action = await dispatcher.dispatch("/skills")

    assert action is DispatchAction.HANDLED
    choose.assert_awaited_once_with(runtime)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("result", "error", "expected"),
    [
        (True, None, "■ Helix MCP ready"),
        (None, AppError("startup timeout"), "■ Helix MCP failed\n└ startup timeout"),
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
    except (AppError, Exception) as captured:
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
            AppError("port cleanup failed"),
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
    except (AppError, Exception) as captured:
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


def _slash_completions(text: str):
    return SlashCommandCompleter().slash_completions(
        Document(text=text, cursor_position=len(text))
    )
