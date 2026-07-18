# -*- coding: utf-8 -*-

from prompt_toolkit.document import Document

from mind_core.prompting.commands import SlashCommandCompleter


def command_texts(text: str) -> list[str]:
    """返回指定输入下的补全文本。"""
    completer = SlashCommandCompleter()
    return [
        completion.text
        for completion in completer.get_completions(Document(text), None)
    ]


def test_helix_commands_are_top_level_completions() -> None:
    """Helix 操作以四个顶层命令展示。"""
    completions = command_texts("/")

    assert "/helix-link" in completions
    assert "/helix-unlink" in completions
    assert "/helix-home" in completions
    assert "/helix-stop" in completions
    assert "/helix" not in completions


def test_helix_prefix_completes_four_commands() -> None:
    """输入 helix 前缀时只补全四个 Helix 命令。"""
    assert command_texts("/helix") == [
        "/helix-link",
        "/helix-unlink",
        "/helix-home",
        "/helix-stop"
    ]
