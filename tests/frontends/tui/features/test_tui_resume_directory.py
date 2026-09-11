import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest
from prompt_toolkit.keys import Keys

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
