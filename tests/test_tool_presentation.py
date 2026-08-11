# -*- coding: utf-8 -*-

import pytest
from prompt_toolkit.utils import get_cwidth

from mind_app.presentation.renderers.tool import (
    render_generic_tool_result_view,
    render_javascript_result_raw_text,
    render_javascript_result_transcript_view,
    render_javascript_result_view,
    render_native_tool_result_view,
    render_tool_start_view,
)
from mind_app.presentation.batch_views import (
    build_batch_completed_view,
    build_batch_start_view,
)
from mind_app.presentation.renderers.dispatch import (
    render_presentation_raw_view,
    render_presentation_transcript_view,
    render_presentation_view,
)
from mind_app.presentation.styles import (
    ACTION_RUN_STYLE,
    ACTION_TOOL_CALLING_STYLE,
    ACTION_TOOL_INVOKED_STYLE,
    ERROR_DOT_STYLE,
    SUCCESS_DOT_STYLE,
    TOOL_CALLING_DOT_STYLE,
)
from mind_app.presentation.tool_views import (
    build_generic_tool_result_view,
    build_native_tool_result_view,
    build_tool_start_view,
)


def _span_style(block, text: str):
    return next(span.style for span in block.spans if span.text == text)


def _rendered_text(block) -> str:
    return "".join(span.text for span in block.spans) or block.plain_text


def _shell_display_title(block, *, preview: bool) -> str:
    text = _rendered_text(block)
    if not preview:
        return text
    title, separator, _preview = text.partition("\n└ ")
    assert separator
    return title


def test_batch_start_transcript_keeps_full_nested_arguments() -> None:
    command = "echo " + "value-" * 40
    view = build_batch_start_view([(
        "shell_command",
        {
            "command": command,
            "metadata": {"target": "nested-value"},
        },
    )])

    display = render_presentation_view(view)[0].plain_text
    transcript = render_presentation_transcript_view(view)[0].plain_text

    assert command not in display
    assert command in transcript
    assert '"target": "nested-value"' in transcript


def test_batch_views_bound_display_and_keep_complete_transcript() -> None:
    start = build_batch_start_view(
        (
            (
                f"tool_{index}",
                {
                    **{f"argument_{item}": item for item in range(5)},
                    "tail": f"start-tail-{index}",
                },
            )
            for index in range(6)
        )
    )
    completed = build_batch_completed_view(
        (
            (
                f"tool_{index}",
                True,
                "\n".join(
                    f"result-{index}-{line}" for line in range(8)
                ),
            )
            for index in range(6)
        )
    )

    start_display = render_presentation_view(start)[0].plain_text
    start_transcript = render_presentation_transcript_view(start)[0].plain_text
    completed_display = render_presentation_view(completed)[0].plain_text
    completed_transcript = render_presentation_transcript_view(
        completed
    )[0].plain_text

    assert "tool_3" in start_display
    assert "tool_4" not in start_display
    assert "… +2 tools" in start_display
    assert "… +3 args" in start_display
    assert "start-tail-5" in start_transcript

    assert "result-0-4" in completed_display
    assert "result-0-5" not in completed_display
    assert "… +3 lines" in completed_display
    assert "tool_4" not in completed_display
    assert "… +2 tools" in completed_display
    assert "result-5-7" in completed_transcript


def test_tool_start_uses_calling_copy_and_pending_colors() -> None:
    block = render_tool_start_view(build_tool_start_view(
        "remote_tool",
        {"query": "status"},
    ))

    assert block.plain_text == "• Function Calling remote_tool"
    assert _span_style(block, "•") == TOOL_CALLING_DOT_STYLE
    assert _span_style(block, "Function Calling") == ACTION_TOOL_CALLING_STYLE
    assert _span_style(block, "Function Calling").bold


def test_native_shell_start_and_result_use_running_then_ran_titles() -> None:
    arguments = {"command": "echo ready"}
    start = render_tool_start_view(build_tool_start_view(
        "shell_command",
        arguments,
        call_id="shell-call",
    ))
    result = render_native_tool_result_view(build_native_tool_result_view(
        "shell_command",
        arguments,
        ok=True,
        data={"command": "echo ready", "output_lines": ["ready"]},
        call_id="shell-call",
    ))[0]

    assert start.plain_text == "• Running echo ready"
    assert "".join(span.text for span in start.spans) == "• Running echo ready"
    assert _span_style(start, "Running") == ACTION_RUN_STYLE
    assert result.plain_text.startswith("• Ran echo ready\n")


def test_width_aware_short_shell_titles_keep_text_and_action_style() -> None:
    arguments = {"command": "echo ready"}
    start = render_presentation_view(
        build_tool_start_view("shell_command", arguments),
        terminal_width=40,
        measure_width=get_cwidth,
    )[0]
    ran = render_presentation_view(
        build_native_tool_result_view(
            "shell_command",
            arguments,
            ok=True,
            data={"command": "echo ready", "output_lines": ["ready"]},
        ),
        terminal_width=40,
        measure_width=get_cwidth,
    )[0]
    started = render_presentation_view(
        build_native_tool_result_view(
            "exec_command",
            arguments,
            ok=True,
            data={
                "command": "echo ready",
                "status": "running",
                "output_lines": ["pending"],
            },
        ),
        terminal_width=40,
        measure_width=get_cwidth,
    )[0]

    assert _shell_display_title(start, preview=False) == "• Running echo ready"
    assert _shell_display_title(ran, preview=True) == "• Ran echo ready"
    assert _shell_display_title(started, preview=True) == "• Started echo ready"
    assert _span_style(start, "Running") == ACTION_RUN_STYLE
    assert _span_style(ran, "Ran") == ACTION_RUN_STYLE
    assert _span_style(started, "Started") == ACTION_RUN_STYLE


@pytest.mark.parametrize("width", (20, 40, 80, 160))
@pytest.mark.parametrize(
    "command",
    (
        "echo " + "value-" * 40,
        "echo " + "界" * 120,
        "echo " + "🙂" * 120,
        "echo " + "👨\u200d👩\u200d👧\u200d👦" * 40,
        "echo " + "e\u0301" * 240,
    ),
    ids=("ascii", "cjk", "emoji", "zwj", "combining"),
)
def test_shell_titles_use_one_shared_display_width_budget(
    width: int,
    command: str,
) -> None:
    arguments = {"command": command}
    start = render_presentation_view(
        build_tool_start_view("shell_command", arguments),
        terminal_width=width,
        measure_width=get_cwidth,
    )[0]
    ran = render_presentation_view(
        build_native_tool_result_view(
            "shell_command",
            arguments,
            ok=True,
            data={"command": command, "output_lines": ["done"]},
        ),
        terminal_width=width,
        measure_width=get_cwidth,
    )[0]
    started = render_presentation_view(
        build_native_tool_result_view(
            "exec_command",
            arguments,
            ok=True,
            data={
                "command": command,
                "status": "running",
                "output_lines": ["pending"],
            },
        ),
        terminal_width=width,
        measure_width=get_cwidth,
    )[0]

    titles = (
        _shell_display_title(start, preview=False),
        _shell_display_title(ran, preview=True),
        _shell_display_title(started, preview=True),
    )
    prefixes = ("• Running ", "• Ran ", "• Started ")
    summaries = tuple(
        title.removeprefix(prefix)
        for title, prefix in zip(titles, prefixes, strict=True)
    )

    assert all("\n" not in title and "│" not in title for title in titles)
    assert all(get_cwidth(title) <= width for title in titles)
    assert len(set(summaries)) == 1
    assert summaries[0].endswith("…")
    assert not summaries[0][:-1].endswith("\u200d")


def test_width_limited_shell_title_keeps_full_command_projections() -> None:
    command = "echo " + "value-" * 40 + "\n| jq .result"
    start_view = build_tool_start_view(
        "shell_command",
        {"command": command},
    )
    result_view = build_native_tool_result_view(
        "shell_command",
        {"command": command},
        ok=True,
        data={"command": command, "output_lines": ["done"]},
    )

    start_display = render_presentation_view(
        start_view,
        terminal_width=20,
        measure_width=get_cwidth,
    )[0]
    result_display = render_presentation_view(
        result_view,
        terminal_width=20,
        measure_width=get_cwidth,
    )[0]

    assert command not in _rendered_text(start_display)
    assert command not in _rendered_text(result_display)
    assert command in render_presentation_transcript_view(start_view)[0].plain_text
    assert command in render_presentation_transcript_view(result_view)[0].plain_text
    assert render_presentation_raw_view(start_view) == (command,)
    assert command in render_presentation_raw_view(result_view)[0]


@pytest.mark.parametrize("width", (20, 40, 80, 160))
@pytest.mark.parametrize(
    "output_line",
    (
        "value-" * 80,
        "界" * 240,
        "👩\u200d💻" * 120,
    ),
    ids=("ascii", "cjk", "zwj"),
)
@pytest.mark.parametrize("ok", (True, False), ids=("success", "failure"))
def test_shell_preview_lines_use_terminal_display_width(
    width: int,
    output_line: str,
    ok: bool,
) -> None:
    view = build_native_tool_result_view(
        "shell_command",
        {"command": "printf output"},
        ok=ok,
        data={
            "command": "printf output",
            "output_lines": [output_line],
            "exit_code": 0 if ok else 1,
        },
    )

    block = render_presentation_view(
        view,
        terminal_width=width,
        measure_width=get_cwidth,
    )[0]
    display_lines = _rendered_text(block).splitlines()

    assert len(display_lines) == 2
    assert display_lines[1].startswith("└ ")
    assert all(get_cwidth(line) <= width for line in display_lines)
    assert output_line in render_presentation_transcript_view(view)[0].plain_text
    assert output_line in render_presentation_raw_view(view)[0]


def test_tool_result_uses_invoked_copy_and_result_colors() -> None:
    success = render_generic_tool_result_view(build_generic_tool_result_view(
        "remote_tool",
        "complete",
        ok=True,
    ))
    failure = render_generic_tool_result_view(build_generic_tool_result_view(
        "remote_tool",
        "failed",
        ok=False,
    ))

    assert success.plain_text.startswith("• Function Invoked remote_tool")
    assert _span_style(success, "•") == SUCCESS_DOT_STYLE
    assert _span_style(success, "Function Invoked") == ACTION_TOOL_INVOKED_STYLE
    assert _span_style(success, "Function Invoked").bold
    assert _span_style(failure, "•") == ERROR_DOT_STYLE
    assert _span_style(failure, "Function Invoked") == ACTION_TOOL_INVOKED_STYLE


@pytest.mark.parametrize(
    ("view", "expected"),
    (
        (
            build_native_tool_result_view(
                "shell_command",
                {"command": "echo ready"},
                ok=True,
                data={"command": "echo ready", "output_lines": ["ready"]},
                cost_ms=0,
            ),
            "✓ • 0ms",
        ),
        (
            build_native_tool_result_view(
                "shell_command",
                {"command": "exit 1"},
                ok=False,
                data={
                    "command": "exit 1",
                    "exit_code": 1,
                    "output_lines": ["failed"],
                },
                cost_ms=800,
            ),
            "✗ (1) • 800ms",
        ),
        (
            build_native_tool_result_view(
                "exec_command",
                {"command": "sleep 3.5"},
                ok=True,
                data={"command": "sleep 3.5"},
                cost_ms=3500,
            ),
            "✓ • 3.50s",
        ),
    ),
)
def test_command_transcript_appends_codex_status_summary(
    view,
    expected: str,
) -> None:
    display = render_presentation_view(view)[0].plain_text
    transcript = render_presentation_transcript_view(view)[0]

    assert transcript.plain_text.splitlines()[-1] == expected
    assert transcript.spans[-2].text in {"✓", "✗", " (1)"}
    assert transcript.spans[-1].style.dim
    icon = next(span for span in transcript.spans if span.text in {"✓", "✗"})
    assert icon.style.bold
    assert icon.style.foreground == (
        "#6EE7A8" if icon.text == "✓" else "#FF6B6B"
    )
    assert "✓ •" not in display
    assert "✗ •" not in display


def test_non_command_tools_do_not_render_command_status_summary() -> None:
    generic = build_generic_tool_result_view(
        "remote_tool",
        "complete",
        ok=True,
    )
    javascript = build_native_tool_result_view(
        "js_repl",
        {"code": "1 + 1"},
        ok=True,
        data={"output": "2"},
        cost_ms=25,
    )

    assert "✓" not in render_presentation_transcript_view(generic)[0].plain_text
    assert "✓" not in render_presentation_transcript_view(
        javascript,
    )[0].plain_text


def test_command_transcript_without_duration_does_not_invent_status() -> None:
    view = build_native_tool_result_view(
        "shell_command",
        {"command": "echo ready"},
        ok=True,
        data={"command": "echo ready", "output_lines": ["ready"]},
    )

    transcript = render_presentation_transcript_view(view)[0]

    assert transcript.plain_text.splitlines()[-1] == "ready"
    assert "✓" not in transcript.plain_text


def test_shell_result_expands_tabs_without_changing_raw_data() -> None:
    raw_line = "47031FDAQ001MK\tdevice"
    view = build_native_tool_result_view(
        "shell_command",
        {"command": "adb devices"},
        ok=True,
        data={"command": "adb devices", "output_lines": [raw_line]},
    )

    block = render_native_tool_result_view(view)[0]
    rendered_text = "".join(span.text for span in block.spans)

    assert view.data["output_lines"] == [raw_line]
    assert "\t" not in block.plain_text
    assert "\t" not in rendered_text
    assert "47031FDAQ001MK  device" in rendered_text


def test_generic_tool_result_filters_terminal_sequences_from_preview() -> None:
    raw = (
        "47031FDAQ001MK\tdevice"
        "\x1b]52;c;SGVsbG8=\x1b\\"
        "\x1bPprivate\x1b\\"
    )
    view = build_generic_tool_result_view("device_tool", raw, ok=True)

    block = render_generic_tool_result_view(view)
    rendered_text = "".join(span.text for span in block.spans)

    assert view.text == raw
    assert "47031FDAQ001MK  device" in rendered_text
    assert "\t" not in rendered_text
    assert "\x1b" not in rendered_text
    assert "SGVsbG8=" not in rendered_text
    assert "private" not in rendered_text


def test_shell_title_filters_controls_before_shortening() -> None:
    raw_command = "adb\x1b]52;c;payload\x1b\\ devices"
    view = build_native_tool_result_view(
        "shell_command",
        {"command": raw_command},
        ok=True,
        data={"command": raw_command, "output_lines": ["done"]},
    )

    block = render_native_tool_result_view(view, terminal_width=24)[0]
    rendered_text = "".join(span.text for span in block.spans)

    assert view.arguments["command"] == raw_command
    assert "adb devices" in rendered_text
    assert "\x1b" not in rendered_text
    assert "payload" not in rendered_text


def test_js_repl_renders_source_then_result_as_two_card_states() -> None:
    source = (
        "await host.tool(\"shell_command\", {\n"
        "  command: 'Start-Process \"https://example.com\"'\n"
        "});"
    )
    start = build_tool_start_view(
        "js_repl",
        {"code": source, "timeout_ms": 30000},
        call_id="call-js",
    )
    result = build_native_tool_result_view(
        "js_repl",
        {"code": source, "timeout_ms": 30000},
        ok=True,
        data={"output": ""},
    )

    start_block = render_tool_start_view(start)
    result_block = render_native_tool_result_view(result)[0]
    transcript = render_presentation_transcript_view(start)[0]
    completed = render_javascript_result_view(result)
    completed_transcript = render_javascript_result_transcript_view(result)

    rendered_start = "".join(span.text for span in start_block.spans)
    assert "Start-Process" in rendered_start
    assert "Running" not in rendered_start
    assert transcript.plain_text == f"• JavaScript\n{source}"
    assert result_block.plain_text.startswith("• JavaScript\n")
    assert "Ran" not in result_block.plain_text
    assert "Start-Process" not in result_block.plain_text
    assert "JavaScript cell completed." in result_block.plain_text
    assert completed.plain_text.count("• JavaScript") == 1
    assert "\n└ JavaScript cell completed." in completed.plain_text
    assert "await host.tool" in completed_transcript.plain_text
    assert "Start-Process" in completed_transcript.plain_text
    assert "JavaScript cell completed." in completed_transcript.plain_text


def test_js_repl_result_renders_explicit_json_output() -> None:
    source = "await host.tool('probe', {});"
    result = build_native_tool_result_view(
        "js_repl",
        {"code": source},
        ok=True,
        data={"output": '{\n  "output": "nested-ok"\n}'},
    )

    block = render_javascript_result_view(result)
    transcript = render_javascript_result_transcript_view(result)
    raw_text = render_javascript_result_raw_text(result)

    assert block.plain_text.startswith("• JavaScript\n")
    assert source not in block.plain_text
    assert '"output": "nested-ok"' in block.plain_text
    assert "JavaScript cell completed." not in block.plain_text
    assert transcript.plain_text.count("• JavaScript") == 1
    assert source in transcript.plain_text
    assert '"output": "nested-ok"' in transcript.plain_text
    assert raw_text == f'{source}\n{{\n  "output": "nested-ok"\n}}'


def test_js_repl_completed_transcript_keeps_omitted_source_and_output() -> None:
    source = "\n".join(f"console.log({index});" for index in range(24))
    output = "\n".join(f"result-{index}" for index in range(12))
    result = build_native_tool_result_view(
        "js_repl",
        {"code": source},
        ok=True,
        data={"output": output},
    )

    start = render_tool_start_view(build_tool_start_view(
        "js_repl",
        {"code": source},
    ))
    display = render_javascript_result_view(result)
    transcript = render_javascript_result_transcript_view(result)

    assert "… +6 lines" in "".join(span.text for span in start.spans)
    assert "… +4 lines" in display.plain_text
    assert "console.log(23);" not in display.plain_text
    assert "result-11" not in display.plain_text
    assert "console.log(23);" in transcript.plain_text
    assert "result-11" in transcript.plain_text


def test_js_repl_display_removes_multiline_embedding_indent() -> None:
    source = (
        'await host.tool("shell_command", {\n'
        '      command: \'Start-Process "https://example.com"\'\n'
        '    });'
    )
    result = build_native_tool_result_view(
        "js_repl",
        {"code": source},
        ok=True,
        data={"output": ""},
    )

    start = render_tool_start_view(build_tool_start_view(
        "js_repl",
        {"code": source},
    ))
    display = "".join(span.text for span in start.spans)
    transcript = render_javascript_result_transcript_view(result).plain_text

    assert "\n    command:" in display
    assert "\n  });" in display
    assert "\n        command:" not in display
    assert "\n        command:" in transcript
