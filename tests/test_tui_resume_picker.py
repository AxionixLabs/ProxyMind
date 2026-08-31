# -*- coding: utf-8 -*-

import asyncio
from dataclasses import replace
import pytest
from prompt_toolkit.styles import Style
from frontends.tui.contracts.resume import (
    ResumeDensity,
    ResumeFilterMode,
    ResumeLaunchContext,
    ResumeArchiveStatus,
    ResumePickerRequest,
    ResumePreview,
    ResumePreviewStatus,
    ResumeRow,
    ResumeSessionStatus,
    ResumeSortKey,
)
from frontends.tui.rendering.fragments import (
    fragments_text,
    split_formatted_lines,
)
from frontends.tui.rendering.menu.resume_picker import (
    ResumeToolbarFocus,
    change_resume_toolbar_value,
    begin_resume_archive,
    create_resume_picker_state,
    enter_resume_transcript,
    exit_resume_transcript,
    filtered_resume_rows,
    move_resume_transcript,
    focus_resume_toolbar,
    move_resume_selection,
    finish_resume_archive,
    remove_resume_row,
    render_resume_picker,
    set_resume_preview,
    set_resume_query,
    toggle_resume_density,
    toggle_resume_expansion,
)
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.styles import build_tui_application_style
from mind_app.presentation.terminal.capabilities import (
    TerminalCapabilities,
    TerminalColorLevel,
    TerminalIdentity,
    TerminalKind,
    TerminalTheme,
)
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth


def _row(
    suffix: str = "1",
    *,
    title: str = "Inspect the workspace",
    workspace: str = "D:/workspace",
    source: str = "tui",
    branch: str = "",
    status: ResumeSessionStatus = ResumeSessionStatus.ACTIVE,
    created_at_ms: int | None = 1_000,
    updated_at_ms: int | None = 2_000,
) -> ResumeRow:
    return ResumeRow(
        cid=f"cid_test_{suffix}2345678",
        sid=f"sid_test_{suffix}_abcdef",
        title=title,
        workspace=workspace,
        source=source,
        branch=branch,
        status=status,
        created_at_ms=created_at_ms,
        updated_at_ms=updated_at_ms,
    )


def test_resume_picker_contract_defaults_match_existing_session_flow() -> None:
    row = _row()

    request = ResumePickerRequest(rows=(row,))

    assert row.key == (row.cid, row.sid)
    assert request.initial_filter is ResumeFilterMode.CWD
    assert request.initial_sort is ResumeSortKey.UPDATED
    assert request.initial_density is ResumeDensity.DENSE
    assert request.launch_context is ResumeLaunchContext.EXISTING_SESSION
    assert request.preview_loader is None


def test_resume_picker_filters_searches_and_sorts_stably() -> None:
    first = _row("1", title="first", updated_at_ms=2_000)
    second = _row(
        "2",
        title="Needle session",
        workspace="d:\\WORKSPACE\\",
        created_at_ms=1_500,
        updated_at_ms=3_000,
    )
    newest_created = _row(
        "3",
        title="created fallback",
        created_at_ms=4_000,
        updated_at_ms=None,
    )
    state = create_resume_picker_state(ResumePickerRequest(
        rows=(first, second, newest_created),
        filter_workspace="D:/workspace",
    ))

    assert state.filter_mode is ResumeFilterMode.CWD
    assert filtered_resume_rows(state) == (newest_created, second, first)

    searched = set_resume_query(state, "needle session")
    assert filtered_resume_rows(searched) == (second,)
    sanitized = set_resume_query(state, "\x1b[31mneedle session")
    assert sanitized.query == "needle session"
    assert filtered_resume_rows(sanitized) == (second,)

    status_focused = focus_resume_toolbar(state)
    assert status_focused.toolbar_focus is ResumeToolbarFocus.STATUS
    archived = change_resume_toolbar_value(status_focused)
    assert filtered_resume_rows(archived) == ()
    sort_focused = focus_resume_toolbar(status_focused)
    assert sort_focused.toolbar_focus is ResumeToolbarFocus.SORT
    created = change_resume_toolbar_value(sort_focused)
    assert created.sort_key is ResumeSortKey.CREATED
    assert filtered_resume_rows(created) == (newest_created, second, first)


def test_resume_picker_archive_state_keeps_failure_and_removes_success() -> None:
    row = _row()
    state = create_resume_picker_state(ResumePickerRequest(rows=(row,)))
    pending = begin_resume_archive(state, row, restoring=False)

    assert pending.archive_status is ResumeArchiveStatus.PENDING
    assert pending.archive_row_key == row.key

    failed = finish_resume_archive(
        pending,
        row_key=row.key,
        error="archive denied",
    )
    assert failed.archive_status is ResumeArchiveStatus.IDLE
    assert failed.archive_error == "archive denied"
    assert failed.request.rows == (row,)

    removed = remove_resume_row(pending, row_key=row.key)
    assert removed.request.rows == ()
    assert removed.archive_status is ResumeArchiveStatus.IDLE


def test_resume_picker_navigation_density_and_expansion_keep_selection() -> None:
    rows = tuple(_row(str(index), title=f"item {index}") for index in range(1, 7))
    state = create_resume_picker_state(ResumePickerRequest(rows=rows))

    state = move_resume_selection(
        state,
        "end",
        viewport_rows=3,
        width=48,
    )
    assert state.selected == 5
    assert state.scroll_top > 0

    state = toggle_resume_density(state, viewport_rows=6, width=48)
    assert state.density is ResumeDensity.COMFORTABLE
    assert state.selected == 5

    state = toggle_resume_expansion(state)
    assert state.expanded_row_key == filtered_resume_rows(state)[5].key
    state = toggle_resume_expansion(state)
    assert state.expanded_row_key is None


def test_resume_picker_footer_percent_uses_rendered_row_height() -> None:
    rows = tuple(_row(str(index), title=f"item {index}") for index in range(1, 7))
    initial = create_resume_picker_state(ResumePickerRequest(rows=rows))
    end = move_resume_selection(
        initial,
        "end",
        viewport_rows=4,
        width=48,
    )

    initial_text = fragments_text(render_resume_picker(
        initial,
        width=48,
        height=12,
    ))
    end_text = fragments_text(render_resume_picker(
        end,
        width=48,
        height=12,
    ))

    assert "1 / 6 · 0%" in initial_text
    assert "6 / 6 · 100%" in end_text
    initial_lines = split_formatted_lines(render_resume_picker(
        initial,
        width=48,
        height=12,
    ))
    footer_progress = [
        (style, value)
        for line in initial_lines[-4:]
        for style, value in line
        if "1 / 6" in value
    ]
    assert len(footer_progress) == 1
    assert footer_progress[0][0] == "class:resume-picker.rule"


def test_resume_picker_render_matches_codex_chrome_and_bounds() -> None:
    now_ms = 10_000_000
    state = create_resume_picker_state(
        ResumePickerRequest(rows=(
            _row(
                title="Investigate picker expansion",
                created_at_ms=now_ms - 3_600_000,
                updated_at_ms=now_ms - 900_000,
            ),
        )),
        now_ms=now_ms,
    )

    fragments = render_resume_picker(state, width=48, height=14)
    lines = split_formatted_lines(fragments)
    text = fragments_text(fragments)
    wide_text = fragments_text(render_resume_picker(state, width=120, height=14))

    assert len(lines) == 14
    assert all(get_cwidth(fragments_text(line)) == 48 for line in lines)
    assert "Resume a previous session" in text
    assert "Type to search" in text
    assert "Filter:[All]" in text
    assert "Status: [Active] Archived" in wide_text
    assert "Sort:[Updated]" in text
    assert "❯ 15m ago" in text
    assert "1 / 1 · 100%" in text
    assert "^o ^t ^e ↑/↓" not in text
    assert any(
        "class:resume-picker.row.selected" in style
        for style, _value in fragments
    )


def test_resume_picker_footer_distinguishes_keys_from_descriptions() -> None:
    state = create_resume_picker_state(ResumePickerRequest(rows=(_row(),)))

    fragments = render_resume_picker(state, width=120, height=14)

    assert ("class:resume-picker.help.key", "enter") in fragments
    assert any(
        style == "class:resume-picker.help" and value.startswith(" resume")
        for style, value in fragments
    )
    assert ("class:resume-picker.help.key", "ctrl+t") in fragments
    assert any(
        style == "class:resume-picker.help" and value.startswith(" transcript")
        for style, value in fragments
    )


def test_resume_picker_render_handles_preview_states_and_narrow_unicode() -> None:
    row = _row(title="界" * 40)
    state = toggle_resume_expansion(create_resume_picker_state(
        ResumePickerRequest(rows=(row,)),
    ))
    loading = set_resume_preview(state, ResumePreview(
        row_key=row.key,
        status=ResumePreviewStatus.LOADING,
    ))
    ready = set_resume_preview(state, ResumePreview(
        row_key=row.key,
        status=ResumePreviewStatus.READY,
        blocks=((
            ("class:resume-picker.preview.user", "recent user"),
        ),),
    ))

    loading_text = fragments_text(render_resume_picker(
        loading,
        width=32,
        height=28,
    ))
    ready_fragments = render_resume_picker(ready, width=32, height=28)
    ready_text = fragments_text(ready_fragments)
    wide_text = fragments_text(render_resume_picker(
        ready,
        width=72,
        height=28,
    ))

    assert "Loading recent trans" in loading_text
    assert "Conversation:" in ready_text
    assert "recent user" in ready_text
    assert "1970-01-01 00:00:01" in wide_text
    assert all(
        "class:resume-picker.row.selected" not in style
        for style, value in ready_fragments
        if "recent user" in value
    )
    assert all(
        get_cwidth(fragments_text(line)) == 32
        for line in split_formatted_lines(ready_fragments)
    )


def test_resume_picker_comfortable_metadata_keeps_branch_column_fixed() -> None:
    rows = (
        _row("1", workspace="D:/one", branch="main"),
        _row(
            "2",
            workspace="D:/a-much-longer-workspace",
            branch="feature/resume",
        ),
    )
    state = create_resume_picker_state(ResumePickerRequest(
        rows=rows,
        show_workspace=True,
        initial_density=ResumeDensity.COMFORTABLE,
    ))

    lines = split_formatted_lines(render_resume_picker(
        state,
        width=100,
        height=20,
    ))
    branch_lines = [
        fragments_text(line)
        for line in lines
        if "" in fragments_text(line)
    ]

    assert len(branch_lines) == 2
    assert branch_lines[0].index("") == branch_lines[1].index("")


def test_resume_picker_transcript_is_full_screen_and_scrollable() -> None:
    row = _row()
    state = enter_resume_transcript(create_resume_picker_state(
        ResumePickerRequest(rows=(row,)),
    ))
    state = set_resume_preview(state, ResumePreview(
        row_key=row.key,
        status=ResumePreviewStatus.READY,
        blocks=tuple(
            (("class:transcript.user", f"› message {index}"),)
            for index in range(20)
        ),
    ))

    fragments = render_resume_picker(state, width=48, height=12)
    lines = split_formatted_lines(fragments)
    text = fragments_text(fragments)

    assert state.transcript_mode
    assert len(lines) == 12
    assert all(get_cwidth(fragments_text(line)) == 48 for line in lines)
    assert "/ T R A N S C R I P T" in text
    assert "› message 19" in text
    assert "q to quit   esc to edit prev" in text
    assert "Resume a previous session" not in text

    top = move_resume_transcript(
        state,
        "home",
        width=48,
        height=12,
    )
    assert top.transcript_scroll_top == 0
    bottom = move_resume_transcript(
        top,
        "end",
        width=48,
        height=12,
    )
    assert bottom.transcript_scroll_top > 0
    assert bottom.transcript_follow_bottom
    assert not exit_resume_transcript(bottom).transcript_mode


@pytest.mark.parametrize("width", (20, 48, 80, 120))
def test_resume_picker_all_visible_states_fit_codex_widths(width: int) -> None:
    row = _row(title="long 界 title " * 12)
    base = create_resume_picker_state(ResumePickerRequest(rows=(row,)))
    error = set_resume_preview(base, ResumePreview(
        row_key=row.key,
        status=ResumePreviewStatus.ERROR,
        error="preview failed",
    ))
    states = (
        create_resume_picker_state(ResumePickerRequest(rows=())),
        set_resume_query(base, "no matching session"),
        error,
    )

    for state in states:
        fragments = render_resume_picker(state, width=width, height=18)
        lines = split_formatted_lines(fragments)
        assert len(lines) == 18
        assert all(get_cwidth(fragments_text(line)) == width for line in lines)


@pytest.mark.parametrize(
    ("background", "selected_bg", "zebra_bg", "title", "marker"),
    (
        ((0, 0, 0), "1F1F1F", "0E0E0E", "ansicyan", "ansiyellow"),
        ((255, 255, 255), "E0E0E0", "F5F5F5", "006400", "ansimagenta"),
    ),
)
def test_resume_picker_colors_match_codex_theme_blends(
    background,
    selected_bg: str,
    zebra_bg: str,
    title: str,
    marker: str,
) -> None:
    empty = Style.from_dict({})
    style = build_tui_application_style(
        empty,
        empty,
        empty,
        capabilities=TerminalCapabilities(
            identity=TerminalIdentity(
                TerminalKind.WINDOWS_TERMINAL,
                "Windows Terminal",
            ),
            color_level=TerminalColorLevel.TRUECOLOR,
            theme=TerminalTheme(background=background),
        ),
    )

    assert style.get_attrs_for_style_str(
        "class:resume-picker.row.selected"
    ).bgcolor == selected_bg
    assert style.get_attrs_for_style_str(
        "class:resume-picker.row.zebra"
    ).bgcolor == zebra_bg
    assert style.get_attrs_for_style_str(
        "class:resume-picker.title"
    ).color == title
    assert style.get_attrs_for_style_str(
        "class:resume-picker.marker"
    ).color == marker
    key_style = style.get_attrs_for_style_str(
        "class:resume-picker.help.key"
    )
    description_style = style.get_attrs_for_style_str(
        "class:resume-picker.help"
    )
    assert key_style.color == ("DDE7EF" if background == (0, 0, 0) else "20262C")
    assert not key_style.dim
    assert description_style.color == (
        "87919D" if background == (0, 0, 0) else "68737D"
    )
    assert description_style.dim


def test_resume_picker_backgrounds_follow_probed_apple_terminal_theme() -> None:
    empty = Style.from_dict({})
    style = build_tui_application_style(
        empty,
        empty,
        empty,
        capabilities=TerminalCapabilities(
            identity=TerminalIdentity(
                TerminalKind.APPLE_TERMINAL,
                "Apple Terminal",
            ),
            color_level=TerminalColorLevel.TRUECOLOR,
            theme=TerminalTheme(background=(0, 0, 0)),
        ),
    )

    assert style.get_attrs_for_style_str(
        "class:resume-picker.row.selected"
    ).bgcolor == "1F1F1F"
    assert style.get_attrs_for_style_str(
        "class:resume-picker.row.zebra"
    ).bgcolor == "0E0E0E"


class _AlternateScreenOutput(DummyOutput):
    def __init__(self) -> None:
        self.size = Size(rows=18, columns=72)
        self.enter_count = 0
        self.quit_count = 0

    def get_size(self) -> Size:
        return self.size

    def enter_alternate_screen(self) -> None:
        self.enter_count += 1

    def quit_alternate_screen(self) -> None:
        self.quit_count += 1


async def _wait_until(predicate) -> None:
    for _ in range(100):
        if predicate():
            return None
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not reached")


@pytest.mark.anyio
async def test_resume_picker_screen_uses_existing_full_screen_and_restores_focus() -> None:
    output = _AlternateScreenOutput()
    runtime = TuiRuntime(output_obj=output)
    screen = runtime.screen
    row = _row()
    request = ResumePickerRequest(rows=(row,))

    assert screen.set_resume_picker(True, request=request, generation=7)
    assert output.enter_count == 1
    assert screen.resume_picker.active
    assert screen.resume_picker.generation == 7
    assert screen.application.full_screen
    assert screen.application.renderer.full_screen
    assert screen.application.layout.current_control == screen.resume_picker_control
    assert not screen.set_transcript_overlay(True)
    assert not screen.set_mailbox_overlay(True, entry_key="missing")

    screen.resume_picker.finish(row)
    assert await screen.wait_resume_picker() is row
    assert screen.resume_picker.active

    assert screen.set_resume_picker(False)
    assert output.quit_count == 1
    assert not screen.resume_picker.active
    assert not screen.application.full_screen
    assert not screen.application.renderer.full_screen
    assert screen._inline_renderer_state is None
    assert screen.application.layout.current_control == screen.input.control

    assert screen.set_transcript_overlay(True)
    assert not screen.set_resume_picker(
        True,
        request=request,
        generation=8,
    )
    assert output.enter_count == 2
    assert screen.set_transcript_overlay(False)
    assert output.quit_count == 2


def test_resume_picker_screen_open_failure_restores_inline_state(monkeypatch) -> None:
    output = _AlternateScreenOutput()
    runtime = TuiRuntime(output_obj=output)
    screen = runtime.screen

    def fail_open(_request, *, generation):
        _ = generation
        raise RuntimeError("picker render failed")

    monkeypatch.setattr(screen.resume_picker, "open", fail_open)

    with pytest.raises(RuntimeError, match="picker render failed"):
        screen.set_resume_picker(
            True,
            request=ResumePickerRequest(rows=()),
            generation=1,
        )

    assert output.enter_count == 1
    assert output.quit_count == 1
    assert not screen.resume_picker.active
    assert not screen.application.full_screen
    assert not screen.application.renderer.full_screen
    assert screen._inline_renderer_state is None
    assert screen.application.layout.current_control == screen.input.control


@pytest.mark.anyio
async def test_runtime_resume_picker_returns_row_and_restores_scrollback() -> None:
    with create_pipe_input() as pipe_input:
        output = _AlternateScreenOutput()
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        row = _row()
        await runtime.open()
        try:
            task = asyncio.create_task(runtime.view_resume_picker(
                ResumePickerRequest(rows=(row,)),
            ))
            await _wait_until(lambda: runtime.screen.resume_picker.active)
            await _wait_until(lambda: (
                runtime.screen.application.renderer.last_rendered_screen
                is not None
            ))
            rendered = runtime.screen.application.renderer.last_rendered_screen
            assert rendered is not None
            rendered_text = "\n".join(
                "".join(
                    rendered.data_buffer[line][column].char
                    for column in range(output.size.columns)
                )
                for line in range(output.size.rows)
            )
            positions = rendered.visible_windows_to_write_positions
            assert "Resume a previous session" in rendered_text
            assert runtime.screen.input.window not in positions
            assert runtime.screen.transcript_window not in positions

            output.size = Size(rows=12, columns=36)
            runtime.invalidate()
            await _wait_until(lambda: (
                runtime.screen.application.renderer.last_rendered_screen
                is not None
                and runtime.screen.application.renderer.last_rendered_screen.height
                == 12
            ))
            resized = runtime.screen.application.renderer.last_rendered_screen
            assert resized is not None
            assert runtime.screen.application.renderer._last_size == output.size
            resized_lines = split_formatted_lines(
                runtime.screen.resume_picker.fragments()
            )
            assert len(resized_lines) == 12
            assert all(
                get_cwidth(fragments_text(line)) == 36
                for line in resized_lines
            )
            assert runtime.screen.application.layout.current_control == (
                runtime.screen.resume_picker_control
            )

            pipe_input.send_text("\r")
            selected = await asyncio.wait_for(task, timeout=1)

            assert selected is row
            assert output.enter_count == 1
            assert output.quit_count == 1
            assert not runtime.screen.resume_picker.active
            assert not runtime.screen.application.full_screen
            assert runtime.screen.application.layout.current_control == (
                runtime.screen.input.control
            )
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_runtime_resume_picker_preview_and_close_cancel_tasks() -> None:
    class Loader(object):
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def load(self, row, *, width):
            _ = width
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            return ResumePreview(
                row_key=row.key,
                status=ResumePreviewStatus.READY,
            )

    with create_pipe_input() as pipe_input:
        output = _AlternateScreenOutput()
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        loader = Loader()
        row = _row()
        await runtime.open()
        task = asyncio.create_task(runtime.view_resume_picker(
            ResumePickerRequest(rows=(row,), preview_loader=loader),
        ))
        await _wait_until(lambda: runtime.screen.resume_picker.active)

        pipe_input.send_text("\x14")
        await asyncio.wait_for(loader.started.wait(), timeout=1)
        assert runtime.screen.resume_picker.state is not None
        assert runtime.screen.resume_picker.state.preview is not None
        assert (
            runtime.screen.resume_picker.state.preview.status
            is ResumePreviewStatus.LOADING
        )

        await runtime.close()

        assert await asyncio.wait_for(task, timeout=1) is None
        assert loader.cancelled.is_set()
        assert output.quit_count == 1
        assert not runtime.screen.resume_picker.active
        assert not runtime.screen.application.renderer.full_screen


@pytest.mark.anyio
async def test_runtime_resume_picker_archive_keeps_picker_open_after_success() -> None:
    with create_pipe_input() as pipe_input:
        output = _AlternateScreenOutput()
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        row = _row()
        called = asyncio.Event()
        calls = []

        async def archive(selected: ResumeRow) -> None:
            calls.append(selected)
            called.set()

        await runtime.open()
        task = asyncio.create_task(runtime.view_resume_picker(
            ResumePickerRequest(rows=(row,), archive_session=archive),
        ))
        await _wait_until(lambda: runtime.screen.resume_picker.active)

        pipe_input.send_text("\x01")
        await asyncio.wait_for(called.wait(), timeout=1)
        await _wait_until(lambda: (
            runtime.screen.resume_picker.state is not None
            and runtime.screen.resume_picker.state.request.rows == ()
        ))

        assert calls == [row]
        assert not task.done()
        await runtime.close()
        assert await asyncio.wait_for(task, timeout=1) is None


@pytest.mark.anyio
async def test_runtime_resume_picker_unarchives_before_returning_row() -> None:
    with create_pipe_input() as pipe_input:
        output = _AlternateScreenOutput()
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        row = _row(status=ResumeSessionStatus.ARCHIVED)
        calls = []

        async def unarchive(selected: ResumeRow) -> ResumeRow:
            calls.append(selected)
            return replace(selected, status=ResumeSessionStatus.ACTIVE)

        await runtime.open()
        task = asyncio.create_task(runtime.view_resume_picker(
            ResumePickerRequest(
                rows=(row,),
                initial_status=ResumeSessionStatus.ARCHIVED,
                unarchive_session=unarchive,
            ),
        ))
        await _wait_until(lambda: runtime.screen.resume_picker.active)

        pipe_input.send_text("\r")
        selected = await asyncio.wait_for(task, timeout=1)

        assert calls == [row]
        assert selected is not None
        assert selected.status is ResumeSessionStatus.ACTIVE
        await runtime.close()


@pytest.mark.anyio
async def test_runtime_resume_picker_opens_full_screen_transcript_and_returns_to_list() -> None:
    class Loader(object):
        async def load(self, row, *, width):
            _ = width
            return ResumePreview(
                row_key=row.key,
                status=ResumePreviewStatus.READY,
                blocks=(
                    (("class:transcript.user", "› hello"),),
                    (("class:transcript.assistant", "Hello from transcript"),),
                ),
            )

    with create_pipe_input() as pipe_input:
        output = _AlternateScreenOutput()
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        row = _row()
        await runtime.open()
        task = asyncio.create_task(runtime.view_resume_picker(
            ResumePickerRequest(rows=(row,), transcript_loader=Loader()),
        ))
        try:
            await _wait_until(lambda: runtime.screen.resume_picker.active)
            pipe_input.send_text("\x14")
            await _wait_until(lambda: (
                runtime.screen.resume_picker.state is not None
                and runtime.screen.resume_picker.state.transcript_mode
                and runtime.screen.resume_picker.state.preview is not None
                and runtime.screen.resume_picker.state.preview.status
                is ResumePreviewStatus.READY
            ))
            text = fragments_text(runtime.screen.resume_picker.fragments())
            assert "/ T R A N S C R I P T" in text
            assert "› hello" in text
            assert "Hello from transcript" in text
            assert "Resume a previous session" not in text

            pipe_input.send_text("q")
            await _wait_until(lambda: (
                runtime.screen.resume_picker.state is not None
                and not runtime.screen.resume_picker.state.transcript_mode
            ))
            assert "Resume a previous session" in fragments_text(
                runtime.screen.resume_picker.fragments()
            )
            pipe_input.send_text("\x14")
            await _wait_until(lambda: (
                runtime.screen.resume_picker.state is not None
                and runtime.screen.resume_picker.state.transcript_mode
            ))
            pipe_input.send_text("\x1b")
            await _wait_until(lambda: (
                runtime.screen.resume_picker.state is not None
                and not runtime.screen.resume_picker.state.transcript_mode
            ))
            pipe_input.send_text("\x03")
            assert await asyncio.wait_for(task, timeout=1) is None
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_resume_picker_empty_enter_search_escape_and_ctrl_c_are_modal() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=_AlternateScreenOutput(),
        )
        await runtime.open()
        try:
            task = asyncio.create_task(runtime.view_resume_picker(
                ResumePickerRequest(rows=()),
            ))
            await _wait_until(lambda: runtime.screen.resume_picker.active)

            pipe_input.send_text("\r")
            await asyncio.sleep(0.05)
            assert not task.done()

            pasted = "needle\nsession"
            pipe_input.send_text(f"\x1b[200~{pasted}\x1b[201~")
            await _wait_until(lambda: (
                runtime.screen.resume_picker.state is not None
                and runtime.screen.resume_picker.state.query == "needle session"
            ))

            pipe_input.send_text("\x1b")
            await _wait_until(lambda: (
                runtime.screen.resume_picker.state is not None
                and runtime.screen.resume_picker.state.query == ""
            ))
            assert not task.done()

            pipe_input.send_text("\x03")
            assert await asyncio.wait_for(task, timeout=1) is None
            assert runtime.active
            assert runtime.screen.input.buffer.text == ""
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_resume_picker_screen_close_failure_still_restores_inline_state(
    monkeypatch,
) -> None:
    output = _AlternateScreenOutput()
    runtime = TuiRuntime(output_obj=output)
    screen = runtime.screen
    assert screen.set_resume_picker(
        True,
        request=ResumePickerRequest(rows=()),
        generation=1,
    )

    def fail_invalidate() -> None:
        raise RuntimeError("picker close render failed")

    monkeypatch.setattr(screen.resume_picker, "_invalidate", fail_invalidate)

    with pytest.raises(RuntimeError, match="picker close render failed"):
        screen.set_resume_picker(False)

    assert output.quit_count == 1
    assert not screen.resume_picker.active
    assert not screen.application.full_screen
    assert not screen.application.renderer.full_screen
    assert screen._inline_renderer_state is None
    assert screen.application.layout.current_control == screen.input.control
