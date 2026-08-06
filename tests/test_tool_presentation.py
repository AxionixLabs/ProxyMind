# -*- coding: utf-8 -*-

from mind_app.presentation.renderers.tool import (
    render_generic_tool_result_view,
    render_javascript_result_raw_text,
    render_javascript_result_transcript_view,
    render_javascript_result_view,
    render_native_tool_result_view,
    render_tool_start_view,
)
from mind_app.presentation.batch_views import build_batch_start_view
from mind_app.presentation.renderers.dispatch import (
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
