# -*- coding: utf-8 -*-

from pathlib import Path
import asyncio
import io
import threading
import time
from unittest.mock import AsyncMock

import pytest
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import DummyOutput

from mind_core.config_session import ConfigSession
from mind_core.config_store import ConfigStore
from mind_core.skills import SkillSpec
from mind_app.tui.core.input import TuiInputModel
from mind_app.tui.core.menu import TUI_MENU_STYLE, TuiMenu
from mind_app.tui.core.models import MenuEmptyAcceptAction
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.features import skills as skills_feature
from mind_app.tui.rendering.fragments import fragments_text
from mind_app.tui.prompting import files as file_search_module
from mind_app.tui.prompting.files import FileSearchManager
from mind_app.tui.prompting.skills import (
    SkillTokenLexer,
    skill_match_score,
)


def _skill(tmp_path: Path, name: str = "APP QA") -> SkillSpec:
    """构造菜单测试使用的 skill。"""
    return SkillSpec(
        name=name,
        description="通过截图测试灯具业务。",
        source="project",
        root=tmp_path / name,
        entry=tmp_path / name / "SKILL.md",
    )


async def _wait_for_file_completions(
    runtime: TuiRuntime,
    document: Document,
    *,
    minimum: int = 1,
) -> tuple:
    """等待指定 `@` 查询的后台文件快照。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 2.0
    while loop.time() < deadline:
        completions = runtime.input_model.completion_menu_completions(document)
        if completions is not None and len(completions) >= minimum:
            return completions
        await asyncio.sleep(0.01)
    raise AssertionError("file completions did not become available")


@pytest.mark.anyio
async def test_skills_root_opens_list_in_main_input(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = _skill(tmp_path)
    monkeypatch.setattr(skills_feature, "available_skills", lambda: (skill,))

    runtime = TuiRuntime()
    runtime.input_model.set_skills((skill,))
    runtime.select_menu = AsyncMock(return_value="list")
    session = ConfigSession(ConfigStore(tmp_path / "config.toml"))

    assert await skills_feature.choose_skill(runtime, session) is None

    root_request = runtime.select_menu.await_args.args[0]
    assert root_request.help_text == "Choose an action"
    assert [option.label for option in root_request.options] == [
        "List skills",
        "Enable/Disable Skills",
    ]
    assert root_request.options[0].detail == (
        "Tip: press @ to open this list directly."
    )
    assert runtime.screen.input.buffer.text == "@"
    assert runtime.screen.input.buffer.cursor_position == 1


@pytest.mark.anyio
async def test_manage_skills_toggles_persist_and_refresh_input_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    skill = _skill(tmp_path)
    monkeypatch.setattr(skills_feature, "available_skills", lambda: (skill,))
    monkeypatch.setattr(
        skills_feature,
        "configured_skills",
        lambda config: () if config["skills"]["disabled"] else (skill,),
    )

    store = ConfigStore(tmp_path / "config.toml")
    session = ConfigSession(store)
    runtime = TuiRuntime()
    runtime.input_model.set_skills((skill,))
    requests = []

    async def select_menu(request):
        requests.append(request)
        return "manage" if len(requests) == 1 else None

    runtime.select_menu = select_menu
    await skills_feature.choose_skill(runtime, session)

    manage_request = requests[1]
    assert manage_request.title == "Enable/Disable Skills"
    assert manage_request.help_text.startswith("Turn skills on or off")
    assert manage_request.footer_hint == (
        "Press space or enter to toggle; esc to close"
    )
    assert manage_request.search_help_text == "Type to search skills"
    assert manage_request.search_prompt_prefix == "> "
    assert manage_request.search_prompt_style == (
        "class:tui-menu.search.placeholder"
    )
    assert manage_request.search_query_style == ""
    assert manage_request.search_empty_text == "no matches"
    assert manage_request.empty_accept_action is MenuEmptyAcceptAction.IGNORE
    assert not manage_request.separate_options
    assert manage_request.options[0].label == "[x] APP QA"
    assert manage_request.options[0].detail == "通过截图测试灯具业务。"

    manage_request.options[0].on_select()

    assert store.read_raw()["skills"]["disabled"] == ["APP QA"]
    assert runtime.input_model.skills == ()

    manage_request.options[0].on_select()

    assert store.read_raw()["skills"]["disabled"] == []
    assert runtime.input_model.skills == (skill,)


@pytest.mark.anyio
async def test_manage_skills_uses_compact_search_layout_and_empty_state(
    tmp_path: Path,
) -> None:
    skill = _skill(tmp_path, "Browser")
    request = skills_feature._manage_request(
        (skill,),
        {"browser": True},
        lambda _skill: None,
    )
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 80,
    )
    task = asyncio.create_task(menu.request(request))
    await asyncio.sleep(0)

    assert fragments_text(menu.fragments()) == "\n".join((
        "  Enable/Disable Skills",
        "  Turn skills on or off. Your changes are saved automatically.",
        "  ",
        "  Type to search skills",
        "  > ",
        "\u203a [x] Browser  通过截图测试灯具业务。",
        "  ",
        "  Press space or enter to toggle; esc to close",
    ))

    menu._update_query("q")
    fragments = menu.fragments()
    assert fragments_text(fragments) == "\n".join((
        "  Enable/Disable Skills",
        "  Turn skills on or off. Your changes are saved automatically.",
        "  ",
        "  Type to search skills",
        "  > q",
        "  no matches",
        "  ",
        "  Press space or enter to toggle; esc to close",
    ))
    assert ("class:tui-menu.search.placeholder", "> ") in fragments
    assert ("class:tui-menu.search", "q") in fragments
    assert (
        "class:tui-menu.search.empty",
        "  no matches",
    ) in fragments
    enter = next(
        binding.handler
        for binding in menu.key_bindings.bindings
        if binding.keys == (Keys.Enter,)
    )
    enter(None)
    assert not task.done()

    help_style = TUI_MENU_STYLE.get_attrs_for_style_str(
        "class:tui-menu.search.placeholder"
    )
    empty_style = TUI_MENU_STYLE.get_attrs_for_style_str(
        "class:tui-menu.search.empty"
    )
    assert help_style.color == empty_style.color
    assert help_style.dim
    assert empty_style.dim
    assert empty_style.italic

    menu.cancel()
    assert await task is None


@pytest.mark.anyio
async def test_manage_skills_search_filters_and_orders_by_name_score(
    tmp_path: Path,
) -> None:
    skills = (
        SkillSpec(
            name="a-b-c",
            description="First description",
            source="project",
            root=tmp_path / "a-b-c",
            entry=tmp_path / "a-b-c" / "SKILL.md",
        ),
        SkillSpec(
            name="abacus",
            description="Second description",
            source="project",
            root=tmp_path / "abacus",
            entry=tmp_path / "abacus" / "SKILL.md",
        ),
        SkillSpec(
            name="myabc",
            description="abc only appears in the description",
            source="project",
            root=tmp_path / "myabc",
            entry=tmp_path / "myabc" / "SKILL.md",
        ),
        SkillSpec(
            name="Browser",
            description="description-only-needle",
            source="project",
            root=tmp_path / "Browser",
            entry=tmp_path / "Browser" / "SKILL.md",
        ),
        SkillSpec(
            name="xray",
            description="Lowercase tie",
            source="project",
            root=tmp_path / "xray",
            entry=tmp_path / "xray" / "SKILL.md",
        ),
        SkillSpec(
            name="Xylophone",
            description="Uppercase tie",
            source="project",
            root=tmp_path / "Xylophone",
            entry=tmp_path / "Xylophone" / "SKILL.md",
        ),
    )
    request = skills_feature._manage_request(
        skills,
        {skill.name.casefold(): True for skill in skills},
        lambda _skill: None,
    )
    menu = TuiMenu(
        invalidate=lambda: None,
        focus_menu=lambda: None,
        focus_input=lambda: None,
        get_width=lambda: 100,
    )
    task = asyncio.create_task(menu.request(request))
    await asyncio.sleep(0)

    menu._update_query(" abc ")
    assert menu.state is not None
    _start, matches = menu._visible_options(menu.state)
    assert [option.value.name for option in matches] == [
        "abacus",
        "a-b-c",
        "myabc",
    ]

    menu._update_query("description-only-needle")
    _start, matches = menu._visible_options(menu.state)
    assert matches == ()

    menu._update_query("x")
    _start, matches = menu._visible_options(menu.state)
    assert [option.value.name for option in matches] == [
        "Xylophone",
        "xray",
    ]

    menu.cancel()
    assert await task is None


@pytest.mark.parametrize(
    ("name", "query", "expected"),
    (
        ("abc", "abc", -100),
        ("a-b-c", "abc", -98),
        ("myabc", "abc", 0),
        ("FooBar", "foO", -100),
        ("İstanbul", "is", -99),
        ("ΟΣ", "οσ", -100),
        ("straße", "strasse", None),
    ),
)
def test_skill_match_score_uses_expected_fuzzy_scoring(
    name: str,
    query: str,
    expected: int | None,
) -> None:
    assert skill_match_score(name, query) == expected


def test_at_query_keeps_the_default_input_style() -> None:
    lexer = SkillTokenLexer(skills=lambda: ())
    get_line = lexer.lex_document(Document("@"))
    assert get_line(0) == [("", "@")]

    style = TuiRuntime().input_model.style
    attrs = style.get_attrs_for_style_str("class:skill-token")
    assert attrs.color == "ansicyan"
    assert attrs.bgcolor == ""
    assert not attrs.dim


@pytest.mark.anyio
async def test_at_query_cursor_stays_after_sigil_and_backspace_removes_it(
    tmp_path: Path,
) -> None:
    skill = _skill(tmp_path, "Browser")
    with create_pipe_input() as input_obj:
        runtime = TuiRuntime(input_obj=input_obj, output_obj=DummyOutput())
        runtime.input_model.set_skills((skill,))

        await runtime.open()
        try:
            input_obj.send_text("@")
            await asyncio.sleep(0.05)
            buffer = runtime.screen.input.buffer
            assert buffer.text == "@"
            assert buffer.cursor_position == 1
            assert buffer.complete_state is not None

            input_obj.send_text("\x7f")
            for _ in range(100):
                if buffer.text == "":
                    break
                await asyncio.sleep(0.001)
            assert buffer.text == ""
            assert buffer.cursor_position == 0
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_at_popup_left_and_right_switch_search_mode_footer(
    tmp_path: Path,
) -> None:
    skill = _skill(tmp_path, "Browser")
    with create_pipe_input() as input_obj:
        runtime = TuiRuntime(input_obj=input_obj, output_obj=DummyOutput())
        runtime.input_model.set_skills((skill,))

        await runtime.open()
        try:
            input_obj.send_text("@")
            for _ in range(100):
                if runtime.screen.input.buffer.complete_state is not None:
                    break
                await asyncio.sleep(0.001)
            assert runtime.screen.input.buffer.cursor_position == 1
            assert runtime.input_model.skill_search_mode == "All Results"
            hint = fragments_text(runtime.screen._completion_hint_fragments())
            assert "[All Results]" in hint
            assert "Filesystem Only" in hint

            input_obj.send_text("\x1b[C")
            await asyncio.sleep(0.02)
            assert runtime.input_model.skill_search_mode == "Filesystem Only"
            assert runtime.screen.input.buffer.cursor_position == 1
            assert "[Filesystem Only]" in fragments_text(
                runtime.screen._completion_hint_fragments()
            )

            input_obj.send_text("\x1b[D")
            await asyncio.sleep(0.02)
            assert runtime.input_model.skill_search_mode == "All Results"
            assert runtime.screen.input.buffer.cursor_position == 1
        finally:
            await runtime.close()


def test_at_sigiled_skill_alias_switches_to_dollar_sigil(
    tmp_path: Path,
) -> None:
    skill = _skill(tmp_path, "review")
    runtime = TuiRuntime()
    runtime.input_model.set_skills((skill,))

    completions = runtime.input_model.completion_menu_completions(
        Document("@rev")
    )

    assert completions is not None
    assert [completion.text for completion in completions] == ["$review "]


@pytest.mark.anyio
async def test_at_filesystem_search_filters_and_inserts_paths(
    tmp_path: Path,
) -> None:
    """验证 `@` 文件候选的类型、匹配高亮和插入文本。"""
    (tmp_path / "AGENTS.md").write_text("content", encoding="utf-8")
    (tmp_path / "app").mkdir()

    runtime = TuiRuntime(TuiInputModel(workspace_root=tmp_path))
    buffer = runtime.screen.input.buffer
    buffer.document = Document("@a")
    runtime.input_model.refresh_completion_menu(buffer)
    await _wait_for_file_completions(runtime, buffer.document, minimum=2)
    runtime.input_model.refresh_completion_menu(buffer)

    snapshot = runtime.input_model.token_menu_snapshot(buffer)
    assert snapshot is not None
    assert [item.kind for item in snapshot.items] == [
        "file-mention",
        "directory-mention",
    ]
    assert snapshot.items[0].match_indices == (0,)
    assert [item.meta_text for item in snapshot.items] == [
        "./  File",
        "./  Dir",
    ]

    runtime.input_model._skill_search_mode_index = 1
    runtime.input_model.refresh_completion_menu(buffer)
    completion = next(
        item
        for item in runtime.input_model.completion_menu_completions(
            buffer.document
        )
        if item.display_text == "app"
    )
    runtime.input_model._apply_menu_completion(buffer, completion)
    assert buffer.text == "app "

    empty_buffer = runtime.screen.input.buffer
    empty_buffer.document = Document("@zzz")
    runtime.input_model.refresh_completion_menu(empty_buffer)
    deadline = asyncio.get_running_loop().time() + 2.0
    while (
        runtime.input_model.completion_empty_message(empty_buffer.document)
        != "no matches"
    ):
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("empty file search did not complete")
        await asyncio.sleep(0.01)
    assert runtime.screen._completion_fallback_fragments() == [
        ("class:completion-menu.empty.mention", "  no matches"),
    ]
    empty_style = runtime.input_model.style.get_attrs_for_style_str(
        "class:completion-menu.empty.mention"
    )
    assert empty_style.italic
    assert not empty_style.dim
    runtime.input_model.close_file_search()


@pytest.mark.anyio
async def test_at_file_search_respects_ignore_boundaries(tmp_path: Path) -> None:
    """验证 Git 元数据被排除，同时保留可搜索的普通隐藏文件。"""
    (tmp_path / ".git" / "objects").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "private-target").write_text(
        "metadata",
        encoding="utf-8",
    )
    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "ignored-target.txt").write_text(
        "ignored",
        encoding="utf-8",
    )
    (tmp_path / ".hidden-target.txt").write_text("hidden", encoding="utf-8")
    (tmp_path / "visible-target.txt").write_text("visible", encoding="utf-8")

    runtime = TuiRuntime(TuiInputModel(workspace_root=tmp_path))
    document = Document("@target")
    completions = await _wait_for_file_completions(
        runtime,
        document,
        minimum=2,
    )
    paths = {completion.text.strip().replace("\\", "/") for completion in completions}

    assert "visible-target.txt" in paths
    assert ".hidden-target.txt" in paths
    assert not any(path.startswith(".git/") for path in paths)
    assert not any(path.startswith("ignored/") for path in paths)
    runtime.input_model.close_file_search()


def test_at_file_search_does_not_scan_on_input_thread(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """验证首次文件枚举不会阻塞处理按键的线程。"""
    release = threading.Event()
    entered = threading.Event()
    worker_threads: list[int] = []

    class FakeProcess(object):
        def __init__(self) -> None:
            self.stdout = io.BytesIO(b"alpha.txt\0")

        def poll(self):
            return 0

        def terminate(self) -> None:
            release.set()

        def wait(self) -> int:
            return 0

    def open_search_process(*_args, **_kwargs):
        worker_threads.append(threading.get_ident())
        entered.set()
        release.wait(timeout=1.0)
        return FakeProcess()

    monkeypatch.setattr(file_search_module.shutil, "which", lambda _name: "rg")
    monkeypatch.setattr(file_search_module.subprocess, "Popen", open_search_process)

    search = FileSearchManager()
    timer = threading.Timer(0.5, release.set)
    timer.start()
    started = time.monotonic()
    try:
        assert search.completions("@a", workspace_root=tmp_path) == ()
        elapsed = time.monotonic() - started
        assert entered.wait(timeout=1.0)
        assert elapsed < 0.2
        assert all(
            thread_id != threading.get_ident()
            for thread_id in worker_threads
        )
    finally:
        release.set()
        timer.cancel()
        search.close()


def test_at_file_search_rejects_stale_query_results(tmp_path: Path) -> None:
    """验证快速改写查询后旧快照不会覆盖最新结果。"""
    (tmp_path / "alpha-target.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "beta-target.txt").write_text("beta", encoding="utf-8")

    search = FileSearchManager()
    try:
        search.completions("@alpha", workspace_root=tmp_path)
        search.completions("@beta", workspace_root=tmp_path)

        deadline = time.monotonic() + 2.0
        completions = ()
        while time.monotonic() < deadline:
            completions = search.completions("@beta", workspace_root=tmp_path)
            if any("beta-target.txt" in item.text for item in completions):
                break
            time.sleep(0.01)

        paths = {item.text.strip().replace("\\", "/") for item in completions}
        assert "beta-target.txt" in paths
        assert "alpha-target.txt" not in paths
    finally:
        search.close()


def test_at_plugin_uses_magenta_and_blue_selection_styles(
    tmp_path: Path,
) -> None:
    """验证 `@` Plugin 普通态为洋红色、选中态与 Skill 共用蓝色。"""
    plugin = SkillSpec(
        name="Visualize",
        description="Plugin description",
        source="plugin",
        root=tmp_path / "plugin",
        entry=tmp_path / "plugin" / "SKILL.md",
    )
    skill = _skill(tmp_path, "APP QA")
    runtime = TuiRuntime(TuiInputModel(workspace_root=tmp_path))
    runtime.input_model.set_skills((plugin, skill))
    buffer = runtime.screen.input.buffer
    buffer.document = Document("@")
    runtime.input_model.refresh_completion_menu(buffer)

    snapshot = runtime.input_model.token_menu_snapshot(buffer)
    assert snapshot is not None
    plugin_index = next(
        index
        for index, item in enumerate(snapshot.items)
        if item.kind == "plugin-mention"
    )
    plugin_style = runtime.input_model.style.get_attrs_for_style_str(
        "class:token-menu.plugin-mention"
    )
    plugin_current = runtime.input_model.style.get_attrs_for_style_str(
        "class:token-menu.plugin-mention.current"
    )
    skill_current = runtime.input_model.style.get_attrs_for_style_str(
        "class:token-menu.skill-mention.current"
    )

    assert plugin_style.color == "ansimagenta"
    assert plugin_style.bold is False
    assert plugin_current.color == "ansiblue"
    assert plugin_current.bold
    assert plugin_current == skill_current
    assert plugin_index >= 0
