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
        "Tools · mode=chat total=3 external=1\n"
        "search (external · stdio · 1)\n"
        "  • external_search\n"
        "coding (local · builtin · 2)\n"
        "  • apply_patch\n"
        "  • shell_command"
    )
    assert "\n\n" not in text
    assert gap.type == "tui.gap"
