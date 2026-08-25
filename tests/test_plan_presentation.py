# -*- coding: utf-8 -*-

from mind_app.presentation.models import (
    PlanItemView,
    PlanStepsStartView,
    PlanUpdateView,
    TextSpan,
)
from mind_app.presentation.renderers.plan import (
    render_plan_steps_start_view,
    render_plan_update_view,
)
from mind_app.presentation.text_layout import layout_styled_line
from prompt_toolkit.utils import get_cwidth


def test_plan_update_without_explanation_branches_to_first_item() -> None:
    block = render_plan_update_view(PlanUpdateView(
        explanation="",
        items=(
            PlanItemView(step="检查状态", status="in_progress"),
            PlanItemView(step="实现修复", status="pending"),
        ),
    ))

    assert block.plain_text == (
        "• Updated Plan\n"
        "  └ □ 检查状态\n"
        "    □ 实现修复"
    )
    assert "".join(span.text for span in block.spans) == block.plain_text
    item_prefix, item_text = block.spans[2:4]
    assert item_prefix.style.foreground is None
    assert item_text.style.foreground == "#5EEAD4"


def test_plan_steps_branches_from_title_column() -> None:
    block = render_plan_steps_start_view(PlanStepsStartView(
        loops=1,
        stop_on_fail=True,
        step_count=2,
        tools=("shell_command", "jq"),
        omitted_steps=0,
    ))

    assert block.plain_text == (
        "• Plan Steps\n"
        "  └ loops=1 · steps=2 · stop_on_fail=true\n"
        "    - shell_command\n"
        "    - jq"
    )
    assert "".join(span.text for span in block.spans) == block.plain_text


def test_plan_update_wraps_explanation_and_steps_with_hanging_indent() -> None:
    block = render_plan_update_view(
        PlanUpdateView(
            explanation="explanation " * 10,
            items=(PlanItemView(
                step="implement a long plan step " * 6,
                status="in_progress",
            ),),
        ),
        terminal_width=20,
        measure_width=get_cwidth,
    )
    lines = block.plain_text.splitlines()

    assert all(get_cwidth(line) <= 20 for line in lines)
    assert lines[1].startswith("  └ ")
    assert all(line.startswith("    ") for line in lines[2:])


def test_plan_steps_wraps_tool_names_below_list_marker() -> None:
    block = render_plan_steps_start_view(
        PlanStepsStartView(
            loops=100,
            stop_on_fail=True,
            step_count=20,
            tools=("tool-name-" * 12,),
            omitted_steps=12,
        ),
        terminal_width=20,
        measure_width=get_cwidth,
    )
    lines = block.plain_text.splitlines()

    assert all(get_cwidth(line) <= 20 for line in lines)
    tool_index = next(
        index for index, line in enumerate(lines)
        if line.startswith("    - ")
    )
    assert lines[tool_index + 1].startswith("      ")


def test_styled_layout_does_not_split_combined_display_units() -> None:
    source = ("👨‍👩‍👧‍👦é") * 4
    spans = layout_styled_line(
        [TextSpan(source)],
        first_prefix=TextSpan("  "),
        continuation_prefix=TextSpan("  "),
        terminal_width=12,
        measure_width=get_cwidth,
        hard=True,
    )
    lines = "".join(span.text for span in spans).splitlines()

    assert all(get_cwidth(line) <= 12 for line in lines)
    assert "".join(line[2:] for line in lines) == source
