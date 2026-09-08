from agent.application.views.builders.approval import build_approval_view
from agent.ports.presentation import TextSpan
from agent.ports.presentation import TextStyle
from frontends.terminal.renderers.approval import render_approval_view
from frontends.terminal.semantic_styles import TerminalSemanticRole
from frontends.terminal.semantic_styles import semantic_text_style


def test_approved_command_styles_match_codex_history_cell() -> None:
    """验证审批通过只着色图标，并突出决定、范围和命令。"""
    block = render_approval_view(build_approval_view(
        {
            "tool": "shell_command",
            "arguments": {"command": "git push origin main"},
        },
        decision="accept",
    ))

    assert block.plain_text == (
        "✔ You approved mind to run git push origin main this time"
    )
    assert block.spans == (
        TextSpan(
            "✔ ",
            semantic_text_style(TerminalSemanticRole.SUCCESS),
        ),
        TextSpan("You "),
        TextSpan("approved", TextStyle(bold=True)),
        TextSpan(" mind to run "),
        TextSpan("git push origin main", TextStyle(dim=True)),
        TextSpan(" this time", TextStyle(bold=True)),
    )


def test_denied_command_styles_match_codex_history_cell() -> None:
    """验证审批拒绝使用红色图标、粗体决定和弱化命令。"""
    block = render_approval_view(build_approval_view(
        {
            "tool": "shell_command",
            "arguments": {"command": "git push origin main"},
        },
        decision="decline",
    ))

    assert block.plain_text == (
        "✗ You did not approve mind to run git push origin main"
    )
    assert block.spans == (
        TextSpan(
            "✗ ",
            semantic_text_style(TerminalSemanticRole.FAILURE),
        ),
        TextSpan("You "),
        TextSpan("did not approve", TextStyle(bold=True)),
        TextSpan(" mind to run "),
        TextSpan("git push origin main", TextStyle(dim=True)),
    )
