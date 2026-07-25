# -*- coding: utf-8 -*-

from io import StringIO

import pytest

from mind_app.cli.commands import (
    COMPLETION_SHELLS,
    CompletionCommand,
)
from mind_app.cli.completion import (
    generate_completion_script,
    run_completion_command,
)
from mind_app.cli.arguments import create_cli_parser


@pytest.mark.parametrize("shell", COMPLETION_SHELLS)
def test_completion_scripts_follow_the_registered_command_tree(shell) -> None:
    script = generate_completion_script(shell)

    assert script.endswith("\n")
    assert "resume" in script
    assert "mcp" in script
    assert "--config" in script
    assert all(candidate in script for candidate in COMPLETION_SHELLS)


def test_completion_script_uses_live_parser_options() -> None:
    parser = create_cli_parser()
    command_parsers = dict(parser.registered_command_parsers())
    command_parsers[("completion",)].add_argument("--probe-completion")

    script = generate_completion_script("bash", parser=parser)

    assert "--probe-completion" in script


def test_completion_command_writes_one_complete_script() -> None:
    output = StringIO()

    exit_code = run_completion_command(
        CompletionCommand(shell="zsh"),
        output_stream=output,
    )

    assert exit_code == 0
    assert output.getvalue().startswith("#compdef ")
