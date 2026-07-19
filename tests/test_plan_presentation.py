# -*- coding: utf-8 -*-

from mind_app.presentation.plan_views import (
    build_plan_steps_start_view,
    build_plan_update_view,
)
from mind_app.presentation.rich import (
    render_plan_steps_start_view,
    render_plan_update_view,
)


def test_plan_update_view_normalizes_valid_snapshot() -> None:
    """计划更新 View 归一化说明、步骤文本和状态。"""
    view = build_plan_update_view({
        "explanation": "  Continue implementation.  ",
        "plan": [
            {"step": "  Inspect flow  ", "status": "completed"},
            {"step": "Run tests", "status": "in_progress"},
        ],
    })

    assert view is not None
    assert view.explanation == "Continue implementation."
    assert [(item.step, item.status) for item in view.items] == [
        ("Inspect flow", "completed"),
        ("Run tests", "in_progress"),
    ]

    rendered = render_plan_update_view(view)
    assert rendered.text == (
        "• Updated Plan\n"
        "  └ Continue implementation.\n"
        "    ✔ Inspect flow\n"
        "    □ Run tests"
    )
    assert rendered.preserve_display_parts is True


def test_plan_update_view_rejects_invalid_snapshots() -> None:
    """计划更新 View 拒绝缺失字段和非法状态。"""
    assert build_plan_update_view(None) is None
    assert build_plan_update_view({"explanation": "", "plan": []}) is None
    assert build_plan_update_view({
        "explanation": "",
        "plan": [{"step": "Run tests", "status": "unknown"}],
    }) is None


def test_plan_steps_view_preserves_preview_limits() -> None:
    """计划步骤 View 保持循环归一化、有效步骤计数和预览上限。"""
    view = build_plan_steps_start_view({
        "loops": 0,
        "stop_on_fail": False,
        "steps": [
            *({"tool": f"tool_{index}"} for index in range(10)),
            "ignored",
        ],
    })

    assert view.loops == 1
    assert view.stop_on_fail is False
    assert view.step_count == 10
    assert view.tools == tuple(f"tool_{index}" for index in range(8))
    assert view.omitted_steps == 2

    rendered = render_plan_steps_start_view(view)
    assert "steps=10" in rendered.text
    assert "    - tool_7" in rendered.text
    assert "    - tool_8" not in rendered.text
    assert rendered.text.endswith("    ... 2 more")
    assert rendered.preserve_display_parts is True
