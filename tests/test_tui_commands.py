# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock
)

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from engine.errors import AppError
from mind_core.skills import SkillSpec
from mind_app.history.transcript import TranscriptEntry
from mind_app.tui.core.models import TranscriptBacktrackRequest
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.features import helix
from mind_app.tui.features.transcript_export import TranscriptExporter
from mind_app.tui.features.skills import choose_skill
from mind_app.tui.features.conversation import ForkLiveStatus
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
    TuiCommandDispatcher,
)


def test_root_command_completion_order_is_stable() -> None:
    completions = _completions("/")

    assert [item.display_text for item in completions] == [
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
        "/helix-mode",
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
        description=(
            "Review the current changes and report every important issue "
            "without omitting details"
        ),
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
    assert request.options[0].detail == (
        "Review the current changes and report e..."
    )


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
        "/helix-mode",
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


def test_command_matching_prioritizes_exact_alias() -> None:
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
        "/helix-mode",
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
    assert stream_command_policy("/helix-mode") == "reject"
    assert stream_command_policy("/mcp start") == "background_barrier"
    assert stream_command_policy("/mcp force") == "background_barrier"
    assert stream_command_policy("/ps") == "local_snapshot"
    assert stream_command_policy("/agent") == "interactive_panel"
    assert stream_command_policy("/compact") == "reject"
    assert stream_command_policy("/hooks") == "reject"
    assert stream_command_policy("/fork") == "reject"
    assert stream_command_policy("/mcp restart") == "reject"
    assert stream_command_policy("! rg foo") == "reject"
    assert stream_command_policy("/quit") == "interrupt"
    assert stream_command_policy("hello") is None
    assert stream_command_policy("$review") is None
    assert stream_command_label("/mcp restart now") == "/mcp restart"


@pytest.mark.parametrize(
    "value",
    ["/permissions", "/MODEL gpt-test", "/mcp start", "/q"],
)
def test_registered_slash_command_inputs_are_resolved(value) -> None:
    assert resolve_slash_command(value) is not None
    assert not is_unrecognized_slash_command(value)


@pytest.mark.parametrize(
    "value",
    ["/今天天气", "/compact later", "/mcp unknown", "/helix-mode app"],
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
@pytest.mark.parametrize("command", ["/helix-mode", "/helix-home"])
async def test_missing_helix_runtime_download_ends_current_command(
    monkeypatch,
    command,
) -> None:
    from mind_app.tui.session import dispatch as dispatch_module

    context = object()
    linked = Mock(side_effect=AssertionError("must not inspect connection"))
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
        require_service_runtime_context=lambda: context,
        is_service_mcp_linked=linked,
    )
    foreground = SimpleNamespace(
        start=Mock(),
        wait=AsyncMock(),
    )
    confirm = AsyncMock(return_value=True)
    monkeypatch.setattr(
        dispatch_module,
        "service_runtime_asset_missing",
        lambda received: received is context,
    )
    monkeypatch.setattr(dispatch_module, "confirm_runtime_download", confirm)
    dispatcher = TuiCommandDispatcher(
        mind,
        SimpleNamespace(),
        SimpleNamespace(),
        foreground,
    )

    action = await dispatcher.dispatch(command)

    assert action is DispatchAction.HANDLED
    confirm.assert_awaited_once_with(dispatcher.runtime, context)
    foreground.start.assert_called_once()
    foreground.wait.assert_awaited_once_with()
    linked.assert_not_called()


@pytest.mark.anyio
async def test_helix_link_remains_the_explicit_connection_command() -> None:
    state = SimpleNamespace(invalidate_workspace=Mock())
    foreground = SimpleNamespace(
        start_helix_link=Mock(),
        wait=AsyncMock(),
    )
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
    )
    dispatcher = TuiCommandDispatcher(
        mind,
        SimpleNamespace(),
        state,
        foreground,
    )

    action = await dispatcher.dispatch("/helix-link")

    assert action is DispatchAction.HANDLED
    foreground.start_helix_link.assert_called_once_with()
    foreground.wait.assert_awaited_once_with()
    state.invalidate_workspace.assert_called_once_with()


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["/helix-mode", "/helix-home"])
async def test_downloaded_but_unlinked_helix_command_does_not_start_runtime(
    monkeypatch,
    command,
) -> None:
    from mind_app.tui.session import dispatch as dispatch_module

    views = []
    foreground = SimpleNamespace(start=Mock(), wait=AsyncMock())
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
        require_service_runtime_context=lambda: object(),
        is_service_mcp_linked=lambda: False,
    )
    choose = AsyncMock()
    monkeypatch.setattr(
        dispatch_module,
        "service_runtime_asset_missing",
        lambda _context: False,
    )
    monkeypatch.setattr(dispatch_module, "choose_helix_tool_profile", choose)
    dispatcher = TuiCommandDispatcher(
        mind,
        SimpleNamespace(),
        SimpleNamespace(),
        foreground,
    )

    action = await dispatcher.dispatch(command)

    assert action is DispatchAction.HANDLED
    foreground.start.assert_not_called()
    choose.assert_not_awaited()
    status = next(view for view in views if view.type == "tui.helix.status")
    assert "".join(
        text for _style, text in status.renderable.fragments
    ) == f"{command} · Helix MCP is not connected"
    hint_style = next(
        style
        for style, text in status.renderable.fragments
        if text == "Helix MCP is not connected"
    )
    assert "bold" not in hint_style


@pytest.mark.anyio
async def test_helix_mode_changes_filter_only_for_linked_runtime(
    monkeypatch,
) -> None:
    from mind_app.tui.session import dispatch as dispatch_module

    views = []
    context = object()
    state = SimpleNamespace(invalidate_workspace=Mock())
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
        require_service_runtime_context=lambda: context,
        is_service_mcp_linked=lambda: True,
        tool_profile_for_turn=lambda: "app",
        set_service_tool_profile=Mock(),
    )
    choose = AsyncMock(return_value="api")
    monkeypatch.setattr(
        dispatch_module,
        "service_runtime_asset_missing",
        lambda _context: False,
    )
    monkeypatch.setattr(dispatch_module, "choose_helix_tool_profile", choose)
    runtime = SimpleNamespace()
    dispatcher = TuiCommandDispatcher(
        mind,
        runtime,
        state,
        SimpleNamespace(),
    )

    action = await dispatcher.dispatch("/helix-mode")

    assert action is DispatchAction.HANDLED
    choose.assert_awaited_once_with(runtime, "app")
    mind.set_service_tool_profile.assert_called_once_with("api")
    state.invalidate_workspace.assert_called_once_with()
    result = next(view for view in views if view.type == "tui.command")
    assert "".join(
        text for _style, text in result.renderable.fragments
    ) == "/helix-mode · api"


@pytest.mark.anyio
async def test_new_conversation_clears_structured_prompt_draft() -> None:
    views = []
    state = SimpleNamespace(clear_pending_prompt_extras=Mock())
    attach = SimpleNamespace(clear_pending_attachments=Mock())
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
        attach=attach,
        reset_conversation=AsyncMock(return_value={
            "cid": "cid_new_12345678",
            "sid": "sid_new_1_abcdef",
        }),
    )
    dispatcher = TuiCommandDispatcher(
        mind,
        SimpleNamespace(),
        state,
        SimpleNamespace(),
    )

    action = await dispatcher.dispatch("/new")

    assert action is DispatchAction.HANDLED
    state.clear_pending_prompt_extras.assert_called_once_with()
    attach.clear_pending_attachments.assert_called_once_with()


@pytest.mark.anyio
async def test_resume_conversation_clears_structured_prompt_draft(
    monkeypatch,
) -> None:
    from mind_app.tui.session import dispatch as dispatch_module

    record = {
        "cid": "cid_old_12345678",
        "sid": "sid_old_1_abcdef",
    }
    state = SimpleNamespace(clear_pending_prompt_extras=Mock())
    attach = SimpleNamespace(clear_pending_attachments=Mock())
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
        attach=attach,
        history_workspace="D:/workspace",
        recent_conversation_sessions=Mock(return_value=[record]),
        read_conversation_transcript=Mock(return_value=()),
        resume_conversation=AsyncMock(return_value=record),
    )
    monkeypatch.setattr(
        dispatch_module,
        "choose_history_session",
        AsyncMock(return_value=record),
    )
    runtime = SimpleNamespace(
        terminal_width=80,
        hyperlinks_enabled=False,
        replace_transcript=Mock(),
    )
    dispatcher = TuiCommandDispatcher(
        mind,
        runtime,
        state,
        SimpleNamespace(),
    )

    await dispatcher._resume_conversation()

    state.clear_pending_prompt_extras.assert_called_once_with()
    attach.clear_pending_attachments.assert_called_once_with()
    mind.read_conversation_transcript.assert_called_once_with(record["sid"])
    restored = runtime.replace_transcript.call_args.args[0]
    assert len(restored) == 1
    assert restored[0].kind == "notice"


@pytest.mark.anyio
async def test_failed_resume_keeps_current_transcript(monkeypatch) -> None:
    from mind_app.tui.session import dispatch as dispatch_module

    record = {
        "cid": "cid_old_12345678",
        "sid": "sid_old_1_abcdef",
    }
    runtime = SimpleNamespace(
        terminal_width=80,
        hyperlinks_enabled=False,
        replace_transcript=Mock(),
    )
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
        attach=SimpleNamespace(clear_pending_attachments=Mock()),
        history_workspace="D:/workspace",
        recent_conversation_sessions=Mock(return_value=[record]),
        read_conversation_transcript=Mock(return_value=()),
        resume_conversation=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        dispatch_module,
        "choose_history_session",
        AsyncMock(return_value=record),
    )
    dispatcher = TuiCommandDispatcher(
        mind,
        runtime,
        SimpleNamespace(clear_pending_prompt_extras=Mock()),
        SimpleNamespace(),
    )

    await dispatcher._resume_conversation()

    runtime.replace_transcript.assert_not_called()


@pytest.mark.anyio
async def test_resumed_transcript_supports_search_export_and_backtrack(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from mind_app.tui.session import dispatch as dispatch_module

    record = {
        "cid": "cid_old_12345678",
        "sid": "sid_old_1_abcdef",
    }

    def entry(event, actor, payload):
        return TranscriptEntry(
            timestamp="2026-08-03T00:00:00.000Z",
            event=event,
            session_id=record["sid"],
            turn_id="turn_one",
            actor=actor,
            payload=payload,
        )

    entries = (
        entry(
            "message.created",
            "user",
            {
                "content": "inspect the workspace",
                "attachments": [{"filename": "notes.txt"}],
                "extras": {"selection": "src/app.py"},
            },
        ),
        entry(
            "tool.started",
            "tool",
            {
                "call_id": "call_1",
                "name": "shell_command",
                "arguments": {"command": "pwd"},
            },
        ),
        entry(
            "tool.completed",
            "tool",
            {
                "call_id": "call_1",
                "result": {"output": "needle workspace"},
            },
        ),
        entry(
            "message.created",
            "assistant",
            {"content": "**Needle found**"},
        ),
    )
    views = []
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
        attach=SimpleNamespace(clear_pending_attachments=Mock()),
        history_workspace="D:/workspace",
        recent_conversation_sessions=Mock(return_value=[record]),
        read_conversation_transcript=Mock(return_value=entries),
        resume_conversation=AsyncMock(return_value=record),
    )
    state = SimpleNamespace(clear_pending_prompt_extras=Mock())
    runtime = TuiRuntime()
    monkeypatch.setattr(
        dispatch_module,
        "choose_history_session",
        AsyncMock(return_value=record),
    )
    dispatcher = TuiCommandDispatcher(
        mind,
        runtime,
        state,
        SimpleNamespace(),
    )

    await dispatcher._resume_conversation()

    assert [block.kind for block in runtime.document.blocks] == [
        "user",
        "operation",
        "assistant",
    ]
    assert sum(
        block.kind == "operation" for block in runtime.document.blocks
    ) == 1

    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay
    overlay.begin_search()
    overlay.append_search_text("needle")
    assert overlay.confirm_search()
    assert overlay.search_result_position == (1, 2)
    overlay.toggle_raw_mode()
    assert overlay.raw_mode
    assert "**Needle found**" in "".join(
        text for _style, text in overlay.fragments()
    )

    exported = TranscriptExporter(tmp_path).export(
        runtime.document.transcript_snapshot().committed_cells,
        "raw",
    )
    exported_text = exported.path.read_text(encoding="utf-8")
    assert exported_text.count("pwd") == 1
    assert exported_text.count("needle workspace") == 1
    assert "**Needle found**" in exported_text

    assert runtime.apply_transcript_backtrack(
        TranscriptBacktrackRequest(
            turn_id="turn_one",
            prompt="inspect the workspace revised",
        )
    )
    assert runtime.document.blocks == []
    assert runtime.screen.input.buffer.text == "inspect the workspace revised"


def test_successful_plain_fork_clears_structured_prompt_draft() -> None:
    state = SimpleNamespace(clear_pending_prompt_extras=Mock())
    attach = SimpleNamespace(clear_pending_attachments=Mock())
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
        attach=attach,
    )
    dispatcher = TuiCommandDispatcher(
        mind,
        SimpleNamespace(),
        state,
        SimpleNamespace(),
    )
    status = ForkLiveStatus()
    status.completed(1)

    dispatcher._finish_conversation_fork(status)

    state.clear_pending_prompt_extras.assert_called_once_with()
    attach.clear_pending_attachments.assert_called_once_with()


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
async def test_dispatcher_opens_agent_panel_without_interrupting(
    monkeypatch,
) -> None:
    from mind_app.tui.session import dispatch as dispatch_module

    menu_called = asyncio.Event()
    runtime = TuiRuntime()
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=lambda _view: None),
        ),
    )

    async def open_menu(received_runtime, received_mind):
        assert received_runtime is runtime
        assert received_mind is mind
        menu_called.set()

    monkeypatch.setattr(dispatch_module, "manage_agents", open_menu)
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
    await asyncio.wait_for(menu_called.wait(), timeout=1)

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

    prepare.assert_awaited_once_with(
        mind,
        "app",
        download_confirmed=False,
    )
    status = next(view for view in views if view.type == "tui.helix.status")
    assert status.renderable.plain_text == expected


@pytest.mark.anyio
async def test_helix_link_switches_profile_without_restarting(
    monkeypatch,
) -> None:
    mind = SimpleNamespace(
        is_service_mcp_linked=lambda: True,
        set_service_tool_profile=Mock(),
    )
    prepare = AsyncMock()
    monkeypatch.setattr(helix, "prepare_tui_service_runtime", prepare)

    linked = await helix.link_helix_runtime(mind, "api")

    assert linked is True
    mind.set_service_tool_profile.assert_called_once_with("api")
    prepare.assert_not_awaited()


@pytest.mark.anyio
async def test_helix_home_opens_only_when_already_linked(monkeypatch) -> None:
    mind = SimpleNamespace(
        is_service_mcp_linked=lambda: True,
        server_manager=SimpleNamespace(url="http://127.0.0.1:9000"),
    )
    open_url = AsyncMock()
    monkeypatch.setattr(helix.FileAssist, "open_url", open_url)

    url = await helix.open_helix_home(mind)

    assert url == "http://127.0.0.1:9000"
    open_url.assert_awaited_once_with(url)


@pytest.mark.anyio
async def test_helix_home_does_not_start_an_unlinked_runtime() -> None:
    mind = SimpleNamespace(is_service_mcp_linked=lambda: False)

    with pytest.raises(AppError, match="Helix MCP is not connected"):
        await helix.open_helix_home(mind)


@pytest.mark.anyio
async def test_helix_mode_menu_uses_current_profile() -> None:
    runtime = TuiRuntime()
    runtime.select_menu = AsyncMock(return_value="api")

    selected = await helix.choose_helix_tool_profile(runtime, "app")

    assert selected == "api"
    request = runtime.select_menu.await_args.args[0]
    assert request.title == "Helix Tool Mode"
    assert request.status == "current=app"
    assert [option.value for option in request.options] == ["app", "api"]
    assert request.selected == 0


@pytest.mark.anyio
async def test_helix_runtime_download_does_not_start_or_link(monkeypatch) -> None:
    runtime = TuiRuntime()
    context = object()
    ensure = AsyncMock(return_value=True)
    mind = SimpleNamespace(
        frontend=SimpleNamespace(runtime=runtime),
        anim_manager=object(),
        design=object(),
        link_service_mcp=Mock(),
    )
    monkeypatch.setattr(helix, "ensure_service_runtime_asset", ensure)

    downloaded = await helix.download_service_runtime(mind, context)

    assert downloaded is True
    ensure.assert_awaited_once()
    assert ensure.await_args.args == (context,)
    assert ensure.await_args.kwargs["explicit_upgrade"] is False
    mind.link_service_mcp.assert_not_called()


def test_helix_home_failure_uses_command_result_block() -> None:
    views = []
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )

    helix.render_helix_home_failure(mind, AppError("open failed"))

    status = next(view for view in views if view.type == "tui.helix.status")
    text = "".join(text for _style, text in status.renderable.fragments)
    assert text == "/helix-home · Failed · open failed"


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
