# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import Mock

from mind_app.tui.features.tools import render_tools_summary


def test_tools_summary_renders_as_one_compact_block() -> None:
    application = SimpleNamespace(emit=Mock())
    tools = [
        {
            "name": "external_search",
            "meta": {
                "external": True,
                "server": "search",
                "transport": "stdio",
            },
        },
        {
            "name": "apply_patch",
            "meta": {"domain": "coding", "class": "builtin"},
        },
        {
            "name": "shell_command",
            "meta": {"domain": "coding", "class": "builtin"},
        },
    ]

    render_tools_summary(application=application, mode="chat", tools=tools)

    summary, gap = (
        call.args[0]
        for call in application.emit.call_args_list
    )
    text = "".join(value for _style, value in summary.renderable.fragments)

    assert summary.type == "tui.tools.summary"
    assert text == (
        "/tools · 3 available · mode=chat external=1\n"
        "search (external · stdio · 1)\n"
        "  • external_search\n"
        "coding (local · builtin · 2)\n"
        "  • apply_patch\n"
        "  • shell_command"
    )
    assert "\n\n" not in text
    assert gap.type == "tui.gap"


def test_tools_summary_uses_current_mode_policy() -> None:
    application = SimpleNamespace(emit=Mock())
    tools = [
        {"name": "apply_patch", "meta": {"domain": "coding"}},
        {"name": "plan_steps", "meta": {"domain": "client", "class": "loop"}},
        {"name": "update_plan", "meta": {"domain": "client", "class": "plan"}},
    ]

    render_tools_summary(application=application, mode="xtra", tools=tools)

    summary = application.emit.call_args_list[0].args[0]
    text = "".join(value for _style, value in summary.renderable.fragments)

    assert "2 available" in text
    assert "apply_patch" in text
    assert "update_plan" in text
    assert "plan_steps" not in text
