# -*- coding: utf-8 -*-

from mind_app.presentation.models import (
    PlanItemView,
    PlanStepsStartView,
    PlanUpdateView,
)
from mind_app.presentation.renderers.plan import (
    render_plan_steps_start_view,
    render_plan_update_view,
)


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
        "└ □ 检查状态\n"
        "  □ 实现修复"
    )
    assert "".join(span.text for span in block.spans) == block.plain_text


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
        "└ loops=1 · steps=2 · stop_on_fail=true\n"
        "  - shell_command\n"
        "  - jq"
    )
    assert "".join(span.text for span in block.spans) == block.plain_text
