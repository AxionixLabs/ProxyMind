# -*- coding: utf-8 -*-

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from mind_app.tui.prompting.commands import (
    SlashCommandCompleter,
    TUI_COMMANDS,
    command_names,
    parameterized_command_texts,
    running_disabled_commands,
)
from mind_app.tui.session.loop import (
    HELP_ITEMS,
    MODE_BY_COMMAND,
)


def test_root_command_completion_order_is_stable() -> None:
    completions = _completions("/")

    assert [item.display_text for item in completions] == [
        "/chat",
        "/fast",
        "/xtra",
        "/new",
        "/resume",
        "/attach",
        "/attachments",
        "/detach",
        "/attach-clear",
        "/permissions",
        "/model",
        "/effort",
        "/preferences",
        "/compact",
        "/tools",
        "/diff",
        "/copy",
        "/ps",
        "/mcp",
        "/helix-link",
        "/helix-unlink",
        "/helix-home",
        "/helix-stop",
        "/skills",
        "/help",
        "/license",
        "/shutdown",
        "/quit",
    ]
    assert next(item for item in completions if item.display_text == "/skills").text == "$"


def test_help_prefix_keeps_existing_alias_order() -> None:
    completions = _completions("/h")

    assert [item.display_text for item in completions] == [
        "/helix-link",
        "/helix-unlink",
        "/helix-home",
        "/helix-stop",
        "/help",
        "/h",
    ]


def test_command_catalog_preserves_dispatch_and_input_policies() -> None:
    assert command_names("quit") == frozenset({"/quit", "/q", "quit", "exit"})
    assert parameterized_command_texts() == ("/attach ", "/detach ", "/model ")
    assert running_disabled_commands() == frozenset({"/new", "/resume"})
    assert MODE_BY_COMMAND == {
        "/chat": "chat",
        "/fast": "fast",
        "/xtra": "xtra",
    }


def test_help_items_are_generated_from_visible_catalog_entries() -> None:
    visible = [command for command in TUI_COMMANDS if command.show_in_help]

    assert [usage for usage, _detail, _style in HELP_ITEMS] == [
        command.usage for command in visible
    ]
    assert "/skills" not in [usage for usage, _detail, _style in HELP_ITEMS]
    assert HELP_ITEMS[-3][0] == "/license, /lic"
    assert HELP_ITEMS[-1][0] == "/quit, /q, quit, exit"


def _completions(text: str):
    return list(SlashCommandCompleter().get_completions(
        Document(text=text, cursor_position=len(text)),
        CompleteEvent(completion_requested=True),
    ))
