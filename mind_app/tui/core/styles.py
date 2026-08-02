# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_core.design.terminal_capabilities import (
    DEGRADED_TERMINAL_CAPABILITIES,
    RgbColor,
    TerminalCapabilities
)
from mind_app.presentation.models import (
    StyledBlock,
    TextSpan,
    TextStyle
)
from mind_app.presentation.terminal_text import sanitize_terminal_hyperlink
from mind_nova import const
from prompt_toolkit.styles import (
    BaseStyle,
    Style,
    merge_styles
)
from ..prompting.commands import (
    canonical_command_label,
    resolve_slash_command
)
from .models import FragmentBlock

MUTED_STYLE   = TextStyle(foreground="#7F8C9A", dim=True)
ACCENT_STYLE  = TextStyle(foreground="#AFC7D8", bold=True)
BRIGHT_STYLE  = TextStyle(foreground="#F4F7FA", bold=True)
BODY_STYLE    = TextStyle(foreground="#DDE7EF")
SUCCESS_STYLE = TextStyle(foreground="#5FD7AF", bold=True)
WARNING_STYLE = TextStyle(foreground="#FFB86B", bold=True)
FAILURE_STYLE = TextStyle(foreground="#FF6B6B", bold=True)
COMMAND_STYLE = TextStyle(foreground="#C4A7E7", bold=True)

ASSISTANT_PREFIX_CLASS = "class:assistant.prefix"

TUI_APPLICATION_OVERRIDES = Style.from_dict({
    "assistant.prefix": "bold dim fg:#7F8C9A",
    "auto-suggestion": "bg:default #5A616A",
    "completion-menu": "bg:default #B8C0C9",
    "completion-menu.completion": "bg:default bold #B8C0C9",
    "completion-menu.completion.current": "bg:default bold #F4F8FB",
    "completion-menu.meta.completion": "bg:default #707A84",
    "completion-menu.meta.completion.current": "bg:default #8FC7EA",
    "completion-menu.empty": "bg:default #59616A",
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
    "ps.separator": "fg:#69727D",
    "ps.meta": "fg:#87919D",
    "ps.warning": "fg:#FFB86B bold",
    "ps.command": "fg:#D8DCE2",
    "ps.help": "fg:#69727D",
    "ps.output": "fg:#A8B1BB dim",
    "ps.stream": "fg:#87919D dim",
    "ps.stream.command": "fg:#8FC7EA",
    "ps.waiting": "fg:#87919D",
    "ps.error": "fg:#FF6B6B",
    "transcript.overlay.title": "fg:#87919D dim",
    "transcript.overlay.rule": "fg:#69727D dim",
    "transcript.overlay.help": "fg:#87919D",
    "transcript.overlay.progress": "fg:#DDE7EF bold",
    "transcript.overlay.filler": "fg:#69727D dim",
    "transcript.overlay.selection": "reverse",
    "transcript.overlay.search-match": "bg:#375A64 fg:#F4F7FA",
    "transcript.overlay.search-prompt": "fg:#8FC7EA bold",
    "transcript.overlay.search-query": "fg:#F4F7FA",
    "transcript.overlay.search-cursor": "fg:#8FC7EA",
    "transcript.overlay.export-success": "fg:#8FD5A6",
    "transcript.overlay.export-error": "fg:#FF8A8A",
})


def _surface_style(
    capabilities: TerminalCapabilities
) -> BaseStyle:
    """根据终端主题创建输入区和审批卡表面样式。"""
    if not capabilities.dynamic_surfaces:
        return Style.from_dict({})

    terminal_background = capabilities.theme.background

    light = _is_light_color(terminal_background)

    surface_background = (
        _blend_color((0, 0, 0), terminal_background, 0.04)
        if light
        else _blend_color((255, 255, 255), terminal_background, 0.12)
    )

    background = f"bg:{_hex_color(surface_background)}"

    styles = {
        "input-surface" : background,
        "approval-card" : background,
    }

    if light:
        styles.update({
            "prompt": "#20262C",
            "prompt.kicker": "bold #596570",
            "prompt.command.slash": "#70408F",
            "placeholder": "#68737D",
            "auto-suggestion": "#737F89",
            "completion-menu.empty": "#68737D",
            "approval-question": "bold #005F87",
            "approval-context": "#53606C",
            "approval-field-label": "bold #43505C",
            "approval-field-value": "#53606C",
            "approval-meta": "#687480",
            "approval-omitted": "#687480",
            "approval-footer": "#687480",
            "approval-option": "#3F4B56",
            "approval-option-selected": "bold #005F87",
            "approval-shortcut": "bold #26323C",
            "approval-shortcut-selected": "bold #004F70",
            "approval-command": "#25303A",
            "approval-command-head": "bold #005F87",
            "approval-command-flag": "#355C7D",
            "approval-command-path": "bold #1F2933",
            "approval-command-string": "#246B4A",
            "approval-command-number": "#755D00",
            "approval-command-operator": "bold #53606C",
        })
    return Style.from_dict(styles)


def _blend_color(
    foreground: RgbColor,
    background: RgbColor,
    ratio: float
) -> RgbColor:
    """按给定比例把前景 RGB 混入背景 RGB。"""
    weight = max(0.0, min(1.0, ratio))
    return typing.cast(
        RgbColor,
        tuple(
            round(front * weight + back * (1.0 - weight))
            for front, back in zip(foreground, background)
        ),
    )


def _is_light_color(color: RgbColor) -> bool:
    """根据相对亮度判断 RGB 是否属于亮色主题。"""
    linear = []

    for component in color:
        value = component / 255
        linear.append(
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
        )

    luminance = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    return luminance > 0.5


def _hex_color(color: RgbColor) -> str:
    """把 RGB 元组转换为 prompt_toolkit 颜色值。"""
    return "#" + "".join(f"{component:02X}" for component in color)


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
    capabilities: TerminalCapabilities = DEGRADED_TERMINAL_CAPABILITIES
) -> BaseStyle:
    """组合 TUI 输入、审批、菜单和主画布样式。"""
    return merge_styles([
        input_style,
        approval_style,
        menu_style,
        TUI_APPLICATION_OVERRIDES,
        _surface_style(capabilities),
    ])


def exit_summary_fragments(session_id: str) -> tuple[tuple[str, str], ...]:
    """生成 TUI 释放终端后的会话恢复提示。"""
    command = f"{const.APP_NAME} resume {session_id}"

    return (
        ("dim fg:#7F8C9A", "■ "),
        ("fg:#DDE7EF", "To continue this session, run "),
        ("fg:#4DE3FF", command),
    )


def styled_block_fragments(
    block: StyledBlock,
    *,
    fallback_style: TextStyle | None = None,
    hyperlinks: bool = False
) -> tuple[tuple[str, str], ...]:
    """把中立展示块转换为 prompt_toolkit 文本片段。"""
    spans = block.spans
    if not spans:
        style = prompt_style(fallback_style or TextStyle())
        return ((style, block.plain_text),) if block.plain_text else ()

    fragments: list[tuple[str, str]] = []
    for span in spans:
        if not span.text:
            continue

        style = prompt_style(
            span.style
            if span.style != TextStyle() or fallback_style is None
            else fallback_style
        )
        hyperlink = (
            sanitize_terminal_hyperlink(span.hyperlink)
            if hyperlinks
            else None
        )
        if hyperlink:
            fragments.append((
                "[ZeroWidthEscape]",
                f"\x1b]8;;{hyperlink}\x1b\\",
            ))
        fragments.append((style, span.text))
        if hyperlink:
            fragments.append(("[ZeroWidthEscape]", "\x1b]8;;\x1b\\"))

    return tuple(fragments)


def assistant_block(block: FragmentBlock) -> FragmentBlock:
    """给助手正文添加项目符号和显式续行缩进。"""
    return FragmentBlock(assistant_fragments(block.fragments))


def assistant_fragments(
    fragments: typing.Iterable[tuple[str, str]]
) -> tuple[tuple[str, str], ...]:
    """返回带助手前缀并移除前导换行的文本片段。"""
    out = [(style, text) for style, text in fragments if text]
    while out:
        style, text = out[0]
        trimmed = text.lstrip("\r\n")
        if trimmed:
            out[0] = style, trimmed
            break
        out.pop(0)
    if not out:
        return ()

    return (
        (ASSISTANT_PREFIX_CLASS, "• "),
        *_assistant_continuation_fragments(
            out,
            indent_style=ASSISTANT_PREFIX_CLASS,
        ),
    )


def _assistant_continuation_fragments(
    fragments: list[tuple[str, str]],
    *,
    indent_style: str,
) -> tuple[tuple[str, str], ...]:
    """在助手正文每个显式续行前补充两个空格。"""
    out: list[tuple[str, str]] = []
    continuation = False

    for style, text in fragments:
        lines      = text.split("\n")
        last_index = len(lines) - 1

        for index, line in enumerate(lines):
            has_newline = index < last_index
            if continuation and (line or has_newline):
                out.append((indent_style, "  "))
                continuation = False
            if line:
                out.append((style, line))
            if has_newline:
                out.append((style, "\n"))
                continuation = True

    return tuple(out)


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


def command_result_block(
    command: str,
    *parts: str | TextSpan
) -> FragmentBlock:
    """生成带规范命令名称的稳定结果块。"""
    return fragment_block(
        TextSpan(f"{canonical_command_label(command)} ", COMMAND_STYLE),
        TextSpan("· ", MUTED_STYLE),
        *parts,
    )


if __name__ == '__main__':
    pass
