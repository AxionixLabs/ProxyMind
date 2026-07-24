# -*- coding: utf-8 -*-

from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.core.styles import query_block


def test_slash_command_uses_purple_transcript_style() -> None:
    block = query_block("/permissions")

    assert block.fragments == (
        ("class:prompt.command.slash", "/permissions"),
    )


def test_non_slash_input_keeps_existing_transcript_style() -> None:
    shell = query_block("!git status")
    message = query_block("hello")

    assert shell.fragments == (("class:prompt", "!git status"),)
    assert message.fragments == (
        ("class:prompt.kicker", "> "),
        ("class:prompt", "hello"),
    )


def test_query_transcript_uses_regular_font_weight() -> None:
    style = TuiRuntime().input_model.style

    assert not style.get_attrs_for_style_str("class:prompt").bold


def test_unknown_slash_text_is_not_styled_as_a_command() -> None:
    block = query_block("/今天天气")

    assert block.fragments == (
        ("class:prompt.kicker", "> "),
        ("class:prompt", "/今天天气"),
    )


def test_unknown_slash_command_stays_editable_and_never_enters_queue() -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer
    buffer.text = "/今天天气"

    keep_text = runtime.submissions.accept_input(buffer)

    assert keep_text
    assert buffer.text == "/今天天气"
    assert runtime.submissions.message_queue.empty()
    assert not runtime.submissions.queued_messages.active
    assert runtime.document.blocks[-1].kind == "notice"
    assert runtime.document.blocks[-1].block.fragments == (
        ("class:input.notice.hint", "•"),
        (
            "class:input.notice.hint",
            " Unrecognized command '/今天天气'. "
            'Type "/" for a list of supported commands.',
        ),
    )
    assert "".join(
        text for _style, text in runtime.document.blocks[-1].block.fragments
    ) == (
        "• Unrecognized command '/今天天气'. "
        'Type "/" for a list of supported commands.'
    )
