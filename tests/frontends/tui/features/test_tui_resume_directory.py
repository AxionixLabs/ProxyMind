import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest
from prompt_toolkit.keys import Keys
from prompt_toolkit.utils import get_cwidth

from frontends.tui.core.keymap import TuiRuntimeKeymap
from frontends.tui.core.menu import TuiMenu
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.features.resume import (
    choose_resume_directory,
    resume_history_session,
)
from infrastructure.config.schema import config_override
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore


@pytest.mark.anyio
@pytest.mark.parametrize(("selection", "target_name", "saved"), [
    ("session", "history", None),
    ("current", "active", None),
    ("remember_session", "history", "session"),
    ("remember_current", "launch", "current"),
])
async def test_four_choices_distinguish_active_and_launch_directories(tmp_path, selection, target_name, saved):
    runtime = TuiRuntime()
    runtime.select_menu = AsyncMock(return_value=selection)
    config = ConfigSession(ConfigStore(tmp_path / "config.toml"), workspace=tmp_path / "launch")
    config.bind_workspace(tmp_path / "active")
    target, warnings = await choose_resume_directory(
        runtime, config, current=config.workspace, history_directory=str(tmp_path / "history"),
    )
    assert target == tmp_path / target_name
    assert not warnings
    assert config.load()["tui"].get("resume_cwd") == saved
    assert len(runtime.select_menu.await_args.args[0].options) == 4
    assert str(config.launch_directory) in runtime.select_menu.await_args.args[0].options[3].label


@pytest.mark.anyio
@pytest.mark.parametrize("width", [50, 80, 180])
async def test_directory_menu_layout_keeps_complete_paths_and_visual_hierarchy(tmp_path, width):
    runtime = TuiRuntime()
    menu = TuiMenu(
        invalidate=lambda: None, focus_menu=lambda: None, focus_input=lambda: None,
        get_width=lambda: width,
    )
    runtime.select_menu = menu.request
    current = tmp_path / "current project"
    config = ConfigSession(ConfigStore(tmp_path / "config.toml"), workspace=current)
    pending = asyncio.create_task(choose_resume_directory(
        runtime, config, current=current,
        history_directory=str(tmp_path / "历史工作区" / "long directory name" / "project"),
    ))
    await asyncio.sleep(0)
    try:
        assert menu.state is not None
        fragments = menu.fragments()
        lines = "".join(text for _style, text in fragments).splitlines()
        first_gap = next(index for index, line in enumerate(lines) if not line.strip())
        assert "".join("".join(lines[:first_gap]).split()) == "Chooseworkingdirectorytoresumethissession"
        assert lines[first_gap + 1].startswith("  Session =")
        assert "  Current = your current working directory" in lines
        first_option = next(index for index, line in enumerate(lines) if line.startswith("› 1. "))
        assert not lines[first_option - 1].strip()
        assert all(get_cwidth(line) <= width for line in lines)
        rendered = "".join("".join(lines).split())
        for option in menu.state.request.options:
            assert "".join(option.label.split()) in rendered
        assert menu.state.request.options[3].label == "Always use current directory"
        style = runtime.screen.application.style
        assert style is not None
        assert not style.get_attrs_for_style_str(fragments[1][0]).bold
        title_emphasis = [style.get_attrs_for_style_str(token) for token, text in fragments if text == "resume"]
        assert len(title_emphasis) == 1 and title_emphasis[0].bold
        body_styles = [style.get_attrs_for_style_str(token) for token, text in fragments if text.startswith("Session =")]
        assert len(body_styles) == 1 and body_styles[0].dim
        footer = menu.footer_fragments()
        for token, text in footer:
            if not text.strip():
                continue
            attributes = style.get_attrs_for_style_str(token)
            assert attributes.bold is (text == "enter")
            assert attributes.dim is (text != "enter")
    finally:
        menu.cancel()
        await pending


@pytest.mark.anyio
async def test_directory_menu_footer_uses_configured_confirmation_key(tmp_path):
    keymap = TuiRuntimeKeymap.from_config({"tui": {"keymap": {"list": {"accept": "f18"}}}})
    menu = TuiMenu(
        invalidate=lambda: None, focus_menu=lambda: None, focus_input=lambda: None,
        get_width=lambda: 100, keymap=keymap.list,
    )
    runtime = TuiRuntime()
    runtime.select_menu = menu.request
    config = ConfigSession(ConfigStore(tmp_path / "config.toml"), workspace=tmp_path)
    pending = asyncio.create_task(choose_resume_directory(
        runtime, config, current=tmp_path, history_directory=str(tmp_path / "history"),
    ))
    await asyncio.sleep(0)
    try:
        assert "".join(text for _style, text in menu.footer_fragments()).strip() == "Press f18 to continue"
    finally:
        menu.cancel()
        await pending


@pytest.mark.anyio
@pytest.mark.parametrize(("strategy", "explicit", "expected"), [
    ("session", False, "history"), ("current", False, "launch"),
    ("session", True, "launch"), (None, True, "launch"),
])
async def test_strategy_and_explicit_directory_precedence(tmp_path, strategy, explicit, expected):
    config = ConfigSession(
        ConfigStore(tmp_path / "config.toml"), workspace=tmp_path / "launch", directory_override=explicit,
    )
    if strategy:
        config.update_user({("tui", "resume_cwd"): strategy})
    config.bind_workspace(tmp_path / "active")
    runtime = TuiRuntime()
    runtime.select_menu = AsyncMock()
    selected, _ = await choose_resume_directory(
        runtime, config, current=config.workspace, history_directory=str(tmp_path / "history"),
    )
    assert selected == tmp_path / expected
    runtime.select_menu.assert_not_awaited()


@pytest.mark.anyio
async def test_missing_metadata_and_equal_directory_skip_prompt(tmp_path):
    config = ConfigSession(ConfigStore(tmp_path / "config.toml"), workspace=tmp_path)
    runtime = TuiRuntime()
    runtime.select_menu = AsyncMock()
    for history in ("", str(tmp_path / "subdir" / "..")):
        selected, _ = await choose_resume_directory(runtime, config, current=tmp_path, history_directory=history)
        assert selected == tmp_path
    runtime.select_menu.assert_not_awaited()
    config.update_user({("tui", "resume_cwd"): "session"})
    with pytest.raises(ValueError, match="Could not determine"):
        await choose_resume_directory(runtime, config, current=tmp_path, history_directory="")
    config.update_user({("tui", "resume_cwd"): "current"})
    assert (await choose_resume_directory(runtime, config, current=tmp_path, history_directory=""))[0] == tmp_path


@pytest.mark.anyio
async def test_save_failure_still_uses_selected_directory(tmp_path):
    config = ConfigSession(ConfigStore(tmp_path / "config.toml"), workspace=tmp_path / "launch")
    config.update_user = Mock(side_effect=OSError("read only configuration"))
    runtime = TuiRuntime()
    runtime.select_menu = AsyncMock(return_value="remember_session")
    target, warnings = await choose_resume_directory(
        runtime, config, current=tmp_path / "active", history_directory=str(tmp_path / "history"),
    )
    assert target == tmp_path / "history"
    assert "read only configuration" in warnings[0]


@pytest.mark.anyio
async def test_remembered_strategy_survives_restart_and_cli_override(tmp_path):
    store = ConfigStore(tmp_path / "config.toml")
    config = ConfigSession(store, workspace=tmp_path / "launch")
    runtime = TuiRuntime()
    runtime.select_menu = AsyncMock(return_value="remember_current")
    await choose_resume_directory(runtime, config, current=tmp_path, history_directory=str(tmp_path / "history"))
    restarted = ConfigSession(store, workspace=tmp_path / "new-launch")
    assert (await choose_resume_directory(runtime, restarted, current=tmp_path, history_directory=""))[0] == tmp_path / "new-launch"
    overridden = ConfigSession(store, (config_override(("tui", "resume_cwd"), "session"),), workspace=tmp_path)
    assert (await choose_resume_directory(runtime, overridden, current=tmp_path, history_directory=str(tmp_path / "history")))[0] == tmp_path / "history"


@pytest.mark.anyio
@pytest.mark.parametrize(("key", "data", "expected"), [
    (Keys.Escape, "", "history"), (Keys.ControlC, "", None),
    (Keys.ControlD, "", None), ("2", "2", "active"),
    (Keys.ControlM, "", "history"),
])
async def test_directory_menu_keyboard_semantics(tmp_path, key, data, expected):
    runtime = TuiRuntime()
    config = ConfigSession(ConfigStore(tmp_path / "config.toml"), workspace=tmp_path / "launch")
    # 直接驱动原生菜单的事件边界；真机终端验证在实施完成后执行。
    runtime.select_menu = runtime.screen.menu.request
    pending = asyncio.create_task(choose_resume_directory(
        runtime, config, current=tmp_path / "active", history_directory=str(tmp_path / "history"),
    ))
    await asyncio.sleep(0)
    event = SimpleNamespace(key_sequence=(SimpleNamespace(key=key),), data=data)
    assert runtime.screen.menu.handle_key_event(event)
    target, _ = await pending
    assert target == (tmp_path / expected if expected else None)


@pytest.mark.anyio
async def test_invalid_selected_directory_preserves_session_and_transcript(tmp_path):
    runtime = TuiRuntime()
    runtime.select_menu = AsyncMock(return_value="session")
    config = ConfigSession(ConfigStore(tmp_path / "config.toml"), workspace=tmp_path)
    host = SimpleNamespace(
        frontend=SimpleNamespace(runtime=runtime), history_workspace=str(tmp_path),
        settings=SimpleNamespace(config=config),
        conversation=SimpleNamespace(cid=None, sid=None, resume=AsyncMock()),
        prepare_workspace=AsyncMock(),
    )
    runtime.replace_transcript = Mock()
    with pytest.raises(ValueError, match="unavailable"):
        await resume_history_session(host, {
            "cid": "cid_test_12345678", "sid": "sid_test_1_abcdef", "workspace": str(tmp_path / "missing"),
        })
    host.conversation.resume.assert_not_awaited()
    host.prepare_workspace.assert_not_awaited()
    runtime.replace_transcript.assert_not_called()


@pytest.mark.anyio
async def test_selecting_active_session_preserves_draft_without_opening_directory_menu(tmp_path):
    runtime = TuiRuntime()
    runtime.select_menu = AsyncMock()
    runtime.screen.input.buffer.text = "unfinished draft"
    record = {"cid": "cid_test_12345678", "sid": "sid_test_1_abcdef"}
    host = SimpleNamespace(
        frontend=SimpleNamespace(runtime=runtime),
        conversation=SimpleNamespace(**record, resume=AsyncMock()),
    )
    assert not await resume_history_session(host, record)
    runtime.select_menu.assert_not_awaited()
    host.conversation.resume.assert_not_awaited()
    assert runtime.screen.input.buffer.text == "unfinished draft"
