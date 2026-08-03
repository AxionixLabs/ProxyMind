# -*- coding: utf-8 -*-

from mind_app.presentation.models import PlanItemView, PlanUpdateView
from mind_app.presentation.renderers.plan import render_plan_update_view


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
