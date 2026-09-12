from dataclasses import replace
from unittest.mock import patch

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
from metadata import const
from tests.frontends.tui.rendering.frame_scenarios import render_next_frame


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


@pytest.mark.parametrize("workspace", ["C:/项目/文件", "C:/🔧/👩‍💻/é", "a" * 100])
@pytest.mark.parametrize("width", [38, 50, 100])
def test_context_is_right_aligned_by_display_columns(width, workspace) -> None:
    fragments = footer_fragments(
        mode=FooterMode.DEFAULT, width=width, model_label="模型",
        permissions_label="Full access", workspace_label=workspace,
        context_label="91% context left",
    )
    text = fragments_text(fragments)
    assert text.startswith(const.APP_DESC)
    assert text.endswith(" 91% context left ")
    assert get_cwidth(text) == width
    assert [(style, value) for style, value in fragments if "footer.context" in style] == [
        ("class:footer.context", "91% context left"),
    ]


def test_width_boundary_preserves_brand_and_hides_context_if_needed() -> None:
    label = "91% context left"
    threshold = get_cwidth(const.APP_DESC) + get_cwidth(label) + 2
    for width in (0, 1, threshold - 1, threshold, threshold + 1):
        text = fragments_text(footer_fragments(
            mode=FooterMode.DEFAULT, width=width, context_label=label,
        ))
        assert get_cwidth(text) <= width
        assert (label in text) == (width >= threshold)


def test_queue_hint_prefers_full_then_short_then_hides_context() -> None:
    label = "91% context left"
    full = "  tab to queue message"
    short = "  tab to queue"
    full_width = get_cwidth(full + label) + 2
    short_width = get_cwidth(short + label) + 2
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
async def test_session_subscription_survives_turn_output_and_closes_with_runtime() -> None:
    with create_pipe_input() as input_obj:
        runtime = TuiRuntime(input_obj=input_obj, output_obj=DummyOutput())
        projection = ContextUsageProjection()
        projection.activate("cid", "sid", initial=True)
        runtime.bind_context_usage(projection)
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
