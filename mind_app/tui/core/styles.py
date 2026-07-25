# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import typing
from mind_app.presentation.models import (
    StyledBlock,
    TextSpan,
    TextStyle
)
from mind_nova import const
from prompt_toolkit.styles import (
    BaseStyle,
    Style,
    merge_styles,
)
from ..prompting.commands import resolve_slash_command
from .models import FragmentBlock

MUTED_STYLE   = TextStyle(foreground="#7F8C9A", dim=True)
ACCENT_STYLE  = TextStyle(foreground="#AFC7D8", bold=True)
BRIGHT_STYLE  = TextStyle(foreground="#F4F7FA", bold=True)
BODY_STYLE    = TextStyle(foreground="#DDE7EF")
SUCCESS_STYLE = TextStyle(foreground="#5FD7AF", bold=True)
WARNING_STYLE = TextStyle(foreground="#FFB86B", bold=True)
FAILURE_STYLE = TextStyle(foreground="#FF6B6B", bold=True)

ASSISTANT_PREFIX_CLASS = "class:assistant.prefix"
TUI_SURFACE_BACKGROUND = "#363B42"

TUI_APPLICATION_OVERRIDES = Style.from_dict({
    "assistant.prefix": "bold dim fg:#7F8C9A",
    "auto-suggestion": "bg:default #5A616A",
    "completion-menu": "bg:default #B8C0C9",
    "completion-menu.completion": "bg:default bold #B8C0C9",
    "completion-menu.completion.current": "bg:default bold #F4F8FB",
    "completion-menu.meta.completion": "bg:default #707A84",
    "completion-menu.meta.completion.current": "bg:default #8FC7EA",
    "queue.label": "bg:default #8A929C bold",
    "queue.marker": "bg:default #7B838E",
    "queue.text": "bg:default #DDE7EF dim",
    "queue.more": "bg:default #7B838E",
    "input.notice.marker": "bg:default #FF5F5F bold",
    "input.notice": "bg:default #FF8A8A",
    "input.notice.hint": "bg:default #DDE7EF",
    "input.notice.example": "bg:default #7F8C9A dim",
    "process-status.exec": "fg:#D8B26E",
    "process-status.separator": "fg:#7B838E",
    "process-status.action": "fg:#8FC7EA bold",
    "process-status.hint": "fg:#7B838E dim",
    "footer.separator": "fg:#7B838E",
    "footer.model": "fg:#F3F5F8",
    "footer.access": "fg:#8FC7EA",
    "footer.access.full": "fg:#D8B26E",
    "footer.workspace": "fg:#8A929C",
    "footer.queue-hint": "fg:#7B838E dim",
    "footer.exit-key": "fg:#C9A86A",
    "footer.exit-hint": "fg:#8A929C",
    "shell.title.dot": "fg:#7F8C9A",
    "shell.title.action": "fg:#8FC7EA bold",
    "shell.title.command": "fg:#F4F7FA",
    "shell.title.suffix": "fg:#7F8C9A",
    "shell.status": "fg:#87919D",
    "shell.stdout": "fg:#D8DCE2",
    "shell.stderr": "fg:#B8C1CB",
    "ps.title": "fg:#F4F7FA bold",
    "ps.meta": "fg:#87919D",
    "ps.help": "fg:#69727D",
    "ps.output": "fg:#D8DCE2",
    "ps.waiting": "fg:#87919D",
    "ps.error": "fg:#FF6B6B",
})


def supports_filled_tui_surfaces(
    environ: typing.Mapping[str, str] | None = None
) -> bool:
    """判断当前终端是否启用满宽表面背景。"""
    env = os.environ if environ is None else environ
    return bool(str(env.get("WT_SESSION") or "").strip()) or (
        str(env.get("TERM_PROGRAM") or "").casefold() == "iterm.app"
        or str(env.get("LC_TERMINAL") or "").casefold() == "iterm2"
    )


def _surface_style(
    environ: typing.Mapping[str, str] | None = None
) -> BaseStyle:
    """创建输入区和审批卡使用的终端表面样式。"""
    if not supports_filled_tui_surfaces(environ):
        return Style.from_dict({})
    background = f"bg:{TUI_SURFACE_BACKGROUND}"
    return Style.from_dict({
        "input-surface": background,
        "approval-card": background,
    })


def prompt_style(style: TextStyle) -> str:
    """把中立文本样式转换为 prompt_toolkit 样式字符串。"""
    parts = [
        name
        for enabled, name in (
            (style.bold, "bold"),
            (style.dim, "dim"),
            (style.italic, "italic"),
            (style.underline, "underline"),
            (style.reverse, "reverse"),
        )
        if enabled
    ]

    if style.foreground:
        parts.append(f"fg:{style.foreground}")
    if style.background:
        parts.append(f"bg:{style.background}")

    return " ".join(parts)


def build_tui_application_style(
    input_style: BaseStyle,
    approval_style: BaseStyle,
    menu_style: BaseStyle,
    *,
    environ: typing.Mapping[str, str] | None = None
) -> BaseStyle:
    """组合 TUI 输入、审批、菜单和主画布样式。"""
    return merge_styles([
        input_style,
        approval_style,
        menu_style,
        TUI_APPLICATION_OVERRIDES,
        _surface_style(environ),
    ])


def exit_summary_fragments() -> tuple[tuple[str, str], ...]:
    """生成 TUI 释放终端后的静态退出摘要。"""
    return (
        ("dim fg:#7F8C9A", "■ "),
        ("fg:#DDE7EF", const.APP_DESC),
        ("dim fg:#7F8C9A", " · session ended"),
    )


def styled_block_fragments(
    block: StyledBlock,
    *,
    fallback_style: TextStyle | None = None,
) -> tuple[tuple[str, str], ...]:
    """把中立展示块转换为 prompt_toolkit 文本片段。"""
    spans = block.spans
    if not spans:
        style = prompt_style(fallback_style or TextStyle())
        return ((style, block.plain_text),) if block.plain_text else ()

    return tuple(
        (
            prompt_style(
                span.style
                if span.style != TextStyle() or fallback_style is None
                else fallback_style
            ),
            span.text,
        )
        for span in spans
        if span.text
    )


def fragment_block(*parts: str | TextSpan) -> FragmentBlock:
    """把有序纯文本或中立文本片段生成 TUI 块。"""
    spans = tuple(
        part if isinstance(part, TextSpan) else TextSpan(str(part))
        for part in parts
        if isinstance(part, TextSpan) or str(part)
    )
    block = StyledBlock(
        plain_text="".join(span.text for span in spans),
        spans=spans,
    )

    return FragmentBlock(styled_block_fragments(block))


def text_block(text: str, style: TextStyle = TextStyle()) -> FragmentBlock:
    """生成单样式 TUI 文本块。"""
    return fragment_block(TextSpan(str(text), style))


def query_block(text: str) -> FragmentBlock:
    """按普通 query 或命令类型生成用户输入块。"""
    value         = str(text).strip()
    lines         = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    slash_command = resolve_slash_command(value) is not None
    command       = slash_command or value.startswith(("!", "$", "\\"))
    text_style    = "class:prompt.command.slash" if slash_command else "class:prompt"

    fragments: list[tuple[str, str]] = []

    for index, line in enumerate(lines):
        if index:
            fragments.append(("", "\n"))
        marker = "" if command else ("› " if index == 0 else "  ")
        if marker:
            fragments.append(("class:prompt.kicker", marker))
        fragments.append((text_style, line))

    return FragmentBlock(tuple(fragments))


if __name__ == '__main__':
    pass
