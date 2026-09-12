import sys
from dataclasses import replace
from unittest.mock import (
    Mock,
    patch,
)

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from agent.application.turns.context_usage import ContextUsageProjection
from agent.application.views.context_usage import ContextUsageView
from agent.ports import OutputSurfaceContext
from agent.protocol.context_usage import ContextUsageRecord
from frontends.terminal.capabilities import TerminalCapabilities
from frontends.terminal.color_support import (
    TerminalColorLevel,
    TerminalColorSupport,
)
from frontends.terminal.identity import (
    TerminalIdentity,
    TerminalKind,
)
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.adapters.session import create_tui_output_session
from frontends.tui.core.styles import (
    build_tui_application_style,
    build_tui_style_transformation,
)
from frontends.tui.rendering.fragments import fragments_text
from frontends.tui.rendering.screen.surfaces import (
    FooterMode,
    context_usage_label,
    footer_fragments,
)
from frontends.tui.rendering.screen.terminal import output_reserved_right_columns
from metadata import const
from tests.frontends.tui.rendering.frame_scenarios import (
    AlternateScreenOutput,
    render_next_frame,
)


def _record() -> ContextUsageRecord:
    return ContextUsageRecord(
        cid="cid", sid="sid", turn_id="turn", event_seq=2, presentation_epoch=1,
        model_context_window=100_000, last_total_tokens=20_000, total_tokens=250_000,
        usage_source="provider", model="test-model", route="responses",
    )


@pytest.mark.parametrize(("value", "expected"), [
    (0, "0 used"), (999, "999 used"), (1000, "1K used"), (1234, "1.23K used"),
    (12_345, "12.3K used"), (250_000, "250K used"), (1_234_567, "1.23M used"),
    (1_000_000_000, "1B used"), (1_000_000_000_000, "1T used"),
])
def test_unknown_window_displays_only_authoritative_total(value, expected) -> None:
    record = replace(_record(), model_context_window=None, total_tokens=value)
    assert context_usage_label(ContextUsageView("known", record)) == expected


def test_footer_statuses_do_not_infer_zero_usage() -> None:
    assert context_usage_label(ContextUsageView("initial")) == "100% context left"
    assert context_usage_label(ContextUsageView("pending", _record())) == ""
    assert context_usage_label(ContextUsageView("unknown", _record())) == ""
    assert context_usage_label(ContextUsageView("known", _record())) == "91% context left"
    record = replace(_record(), last_total_tokens=None, usage_source="unknown")
    assert context_usage_label(ContextUsageView("known", record)) == ""


@pytest.mark.parametrize("reserved", [0, 1])
@pytest.mark.parametrize("physical_width", [38, 50, 120])
@pytest.mark.parametrize("percent", [100, 99, 9, 0])
def test_context_has_two_physical_columns_of_right_margin(physical_width, reserved, percent) -> None:
    width = physical_width - reserved
    label = f"{percent}% context left"
    fragments = footer_fragments(
        mode=FooterMode.QUEUE_SUBMISSION, width=width,
        context_label=label, reserved_right_columns=reserved,
    )
    text = fragments_text(fragments)
    assert text.startswith("  tab to queue")
    assert text.endswith(label + " " * (2 - reserved))
    assert get_cwidth(text) == width
    assert get_cwidth(text.rstrip()) == physical_width - 2
    assert [(style, value) for style, value in fragments if "footer.context" in style] == [
        ("class:footer.context", label),
    ]


def test_width_boundary_preserves_queue_hint_and_hides_context_if_needed() -> None:
    label = "91% context left"
    threshold = len("  tab to queue") + len("100% context left") + 3
    for width in (0, 1, threshold - 1, threshold, threshold + 1):
        text = fragments_text(footer_fragments(
            mode=FooterMode.QUEUE_SUBMISSION, width=width, context_label=label,
        ))
        assert get_cwidth(text) <= width
        assert (label in text) == (width >= threshold)


def test_queue_hint_prefers_full_then_short_then_hides_context() -> None:
    label = "91% context left"
    full = "  tab to queue message"
    short = "  tab to queue"
    full_width = len(full + "100% context left") + 3
    short_width = len(short + "100% context left") + 3
    for width, expected_hint, show_context in (
        (full_width, full, True),
        (full_width - 1, short, True),
        (short_width, short, True),
        (short_width - 1, full, False),
    ):
        text = fragments_text(footer_fragments(
            mode=FooterMode.QUEUE_SUBMISSION, width=width, context_label=label,
        ))
        assert text.startswith(expected_hint)
        assert (label in text) == show_context
        assert get_cwidth(text) <= width


@pytest.mark.parametrize("width", [33, 34, 40, 41, 80])
def test_percentage_digit_changes_and_pending_do_not_move_queue_hint(width) -> None:
    hints = []
    for label in ("100% context left", "99% context left", "9% context left", "", "0% context left"):
        parts = footer_fragments(mode=FooterMode.QUEUE_SUBMISSION, width=width, context_label=label)
        hints.append([(style, text) for style, text in parts if style == "class:footer.queue-hint"])
    assert all(hint == hints[0] for hint in hints)


@pytest.mark.parametrize("workspace", ["C:/项目/文件", "C:/🔧/👩‍💻/é", "a" * 100])
def test_passive_status_line_does_not_display_context(workspace) -> None:
    kwargs = dict(mode=FooterMode.DEFAULT, width=70, model_label="模型", workspace_label=workspace)
    parts = footer_fragments(**kwargs, context_label="100% context left")
    assert parts == footer_fragments(**kwargs)
    assert fragments_text(parts).startswith(const.APP_DESC)
    assert get_cwidth(fragments_text(parts)) <= 70


def test_portable_output_does_not_reserve_an_extra_column() -> None:
    assert output_reserved_right_columns(DummyOutput()) == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows output backends")
def test_windows_output_reserves_one_column_outside_render_width() -> None:
    from prompt_toolkit.output.conemu import ConEmuOutput
    from prompt_toolkit.output.win32 import Win32Output
    from prompt_toolkit.output.windows10 import Windows10_Output

    for backend in (Win32Output, Windows10_Output, ConEmuOutput):
        assert output_reserved_right_columns(Mock(spec=backend)) == 1


@pytest.mark.parametrize("mode", [
    FooterMode.HISTORY_SEARCH, FooterMode.HISTORY_BACKTRACK,
    FooterMode.EXIT_ARMED, FooterMode.HIDDEN,
])
def test_special_modes_own_footer(mode) -> None:
    assert "context left" not in fragments_text(footer_fragments(
        mode=mode, width=100, context_label="91% context left",
    ))


@pytest.mark.parametrize("level", [
    TerminalColorLevel.NONE, TerminalColorLevel.ANSI16, TerminalColorLevel.TRUECOLOR,
])
def test_context_final_style_is_dim_and_not_bold(level) -> None:
    capabilities = TerminalCapabilities(
        identity=TerminalIdentity(TerminalKind.UNKNOWN, "test"),
        color_support=TerminalColorSupport.fixed(level),
    )
    empty = Style.from_dict({})
    style = build_tui_application_style(empty, empty, empty, capabilities=capabilities)
    attrs = build_tui_style_transformation(capabilities).transform_attrs(
        style.get_attrs_for_style_str("class:footer.context"),
    )
    assert attrs.dim
    assert not attrs.bold


@pytest.mark.anyio
async def test_cold_resume_hides_initial_until_target_snapshot_arrives() -> None:
    with create_pipe_input() as input_obj:
        runtime = TuiRuntime(input_obj=input_obj, output_obj=DummyOutput())
        projection = ContextUsageProjection()
        runtime.bind_context_usage(projection, pending=True)
        try:
            assert runtime.screen.context_usage_label == ""
            projection.activate("cid", "sid", initial=False)
            projection.apply(_record())
            assert runtime.screen.context_usage_label == ""
            projection.finish_replay("cid", "sid")
            assert runtime.screen.context_usage_label == "91% context left"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_session_subscription_survives_turn_output_and_closes_with_runtime() -> None:
    with create_pipe_input() as input_obj:
        runtime = TuiRuntime(input_obj=input_obj, output_obj=DummyOutput())
        projection = ContextUsageProjection()
        projection.activate("cid", "sid", initial=True)
        runtime.bind_context_usage(projection)
        runtime.task_state.set_turn_running(True)
        runtime.screen.input.buffer.text = "下一条消息"
        with patch.object(runtime.screen.application.output, "get_size", return_value=Size(rows=16, columns=70)):
            await runtime.open()
            try:
                before = await render_next_frame(runtime)
                input_before = before.visible_windows_to_write_positions[runtime.screen.input.window]
                projection.apply(_record())
                after = await render_next_frame(runtime)
                input_after = after.visible_windows_to_write_positions[runtime.screen.input.window]
                assert (input_after.ypos, input_after.height) == (input_before.ypos, input_before.height)
                assert "91% context left" in fragments_text(runtime.screen._footer_fragments())
                with patch.object(runtime.screen, "invalidate") as invalidate:
                    projection.apply(replace(_record(), event_seq=3, total_tokens=900_000))
                    invalidate.assert_not_called()
                projection.begin_replay("cid", "sid")
                assert runtime.screen.context_usage_label == ""
                projection.finish_replay("cid", "sid")
                assert runtime.screen.context_usage_label == "91% context left"
                output = create_tui_output_session(
                    "", runtime=runtime, animate=False,
                    context=OutputSurfaceContext(
                        surface_id="surface", cid="cid", sid="sid", turn_id="turn", agent_id="root",
                    ),
                )
                await output.open()
                await output.close()
                assert runtime.screen.context_usage_label == "91% context left"
            finally:
                await runtime.close()
        with patch.object(runtime.screen, "invalidate") as invalidate:
            projection.close()
            invalidate.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(("running", "draft", "visible"), [
    (False, "", False), (False, "草稿", False),
    (True, "", False), (True, "   ", False), (True, "草稿", True),
])
async def test_context_requires_running_turn_and_nonempty_draft(running, draft, visible) -> None:
    with create_pipe_input() as input_obj:
        runtime = TuiRuntime(input_obj=input_obj, output_obj=DummyOutput())
        try:
            runtime.task_state.set_turn_running(running)
            runtime.screen.input.buffer.text = draft
            runtime.set_context_usage(ContextUsageView("known", _record()))
            parts = runtime.screen._footer_fragments()
            assert ("context left" in fragments_text(parts)) is visible
            if not visible:
                with patch.object(runtime.screen, "invalidate") as invalidate:
                    runtime.set_context_usage(ContextUsageView("known", replace(_record(), last_total_tokens=30_000)))
                    invalidate.assert_not_called()
        finally:
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["foreground", "pending", "shell"])
async def test_context_is_hidden_outside_running_chat_draft(mode) -> None:
    with create_pipe_input() as input_obj:
        runtime = TuiRuntime(input_obj=input_obj, output_obj=DummyOutput())
        try:
            if mode == "foreground":
                runtime.task_state.set_foreground_running(True)
            elif mode == "pending":
                runtime.task_state.set_turn_start_pending(True)
            else:
                runtime.task_state.set_turn_running(True)
                runtime.input_model.shell_mode = True
            runtime.screen.input.buffer.text = "草稿"
            runtime.set_context_usage(ContextUsageView("known", _record()))
            assert "context left" not in fragments_text(runtime.screen._footer_fragments())
        finally:
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("reserved", [0, 1])
async def test_context_frames_keep_right_edge_height_and_cursor_stable(reserved) -> None:
    with create_pipe_input() as input_obj:
        terminal = AlternateScreenOutput(columns=80 - reserved, rows=18)
        with patch("frontends.tui.core.screen.output_reserved_right_columns", return_value=reserved):
            runtime = TuiRuntime(input_obj=input_obj, output_obj=terminal)
        runtime.task_state.set_turn_running(True)
        runtime.screen.input.buffer.text = "排队草稿"
        await runtime.open()
        try:
            for columns in (80, 41, 34, 25, 80):
                terminal.size = Size(rows=18, columns=columns - reserved)
                positions = []
                for used in (0, 12880, 92080, 100000, 20000):
                    runtime.set_context_usage(ContextUsageView("known", replace(_record(), last_total_tokens=used)))
                    frame = await render_next_frame(runtime)
                    area = frame.visible_windows_to_write_positions[runtime.screen.input.window]
                    positions.append((area.xpos, area.ypos, area.width, area.height, frame.get_cursor_position(runtime.screen.input.window)))
                    cells = [(x, y, cell) for y, row in frame.data_buffer.items()
                             for x, cell in row.items() if "class:footer.context" in cell.style]
                    if columns >= 34:
                        assert cells
                        assert max(x for x, _y, _cell in cells) == columns - 3
                        assert len({y for _x, y, _cell in cells}) == 1
                    else:
                        assert not cells
                assert all(position == positions[0] for position in positions)
            runtime.task_state.set_turn_running(False)
            frame = await render_next_frame(runtime)
            assert not any("class:footer.context" in cell.style for row in frame.data_buffer.values() for cell in row.values())
        finally:
            await runtime.close()
