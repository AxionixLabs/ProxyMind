# -*- coding: utf-8 -*-

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
