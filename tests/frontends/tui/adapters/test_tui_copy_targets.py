# -*- coding: utf-8 -*-

from frontends.tui.adapters.copy_targets import assistant_copy_targets


def test_copy_targets_preserve_source_order_and_exact_code() -> None:
    source = (
        "Intro\r\n\r\n"
        "```powershell\r\n"
        "Write-Output value  \r\n"
        "Write-Output done\t\r\n"
        "```\r\n\r\n"
        "> Keep **formatting**  \r\n"
        "> > Nested quote\r\n"
    )

    targets = assistant_copy_targets(source)

    assert [(target.label, target.text) for target in targets] == [
        (
            "Whole response",
            "Intro\n\n```powershell\nWrite-Output value\n"
            "Write-Output done\n```\n\n> Keep **formatting**\n"
            "> > Nested quote",
        ),
        (
            "powershell code",
            "Write-Output value  \r\nWrite-Output done\t\r\n",
        ),
        (
            "Blockquote",
            "Keep **formatting**  \r\n> Nested quote\r\n",
        ),
    ]


def test_copy_targets_keep_nested_quote_before_nested_code() -> None:
    source = (
        "> **outer**\n"
        "> > _nested_\n"
        "> ```sh\n"
        "> nested()\n"
        "> ```\n"
    )

    targets = assistant_copy_targets(source)

    assert [(target.label, target.text) for target in targets] == [
        ("Whole response", source.rstrip()),
        (
            "Blockquote",
            "**outer**\n> _nested_\n```sh\nnested()\n```\n",
        ),
        ("sh code", "nested()\n"),
    ]


def test_copy_targets_ignore_indented_code_and_code_only_quote() -> None:
    targets = assistant_copy_targets(
        "    indented()\n\n> ```python\n> nested()\n> ```\n"
    )

    assert [target.label for target in targets] == [
        "Whole response",
        "python code",
    ]


def test_copy_target_description_uses_first_nonblank_line_and_72_chars() -> None:
    source = f"\n\n```text\n{'x' * 80}\n```"

    targets = assistant_copy_targets(source)

    assert targets[0].description == "```text"
    assert targets[1].description == "x" * 72
