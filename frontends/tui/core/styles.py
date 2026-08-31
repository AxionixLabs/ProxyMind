# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.presentation.terminal.capabilities import (
    DEGRADED_TERMINAL_CAPABILITIES,
    RgbColor,
    TerminalCapabilities
)
from mind_app.presentation.terminal.palette import (
    best_color,
    is_light_color,
    selection_color,
    semantic_color
)
from agent.ports.presentation import (
    StyledBlock,
    TextSpan,
    TextStyle
)
from metadata import const
from prompt_toolkit.styles import (
    BaseStyle,
    Style,
    merge_styles
)
from ..prompting.commands import (
    canonical_command_label,
    resolve_slash_command
)
from .models import (
    FragmentBlock,
    LineFill
)
from .hyperlinks import terminal_hyperlink_style
from ..rendering.fragments import (
    ZERO_WIDTH_ESCAPE_STYLE,
    clip_fragments,
    join_formatted_lines,
    split_formatted_lines,
    transcript_hint,
    wrap_formatted_lines
)
from ..rendering.text_sanitize import sanitize_fragment_block

# Fragment-level brand colors remain product-specific; class-based styles use the palette resolver.
MUTED_STYLE   = TextStyle(foreground="#7F8C9A", dim=True)
ACCENT_STYLE  = TextStyle(foreground="#AFC7D8", bold=True)
BRIGHT_STYLE  = TextStyle(foreground="#F4F7FA", bold=True)
BODY_STYLE    = TextStyle(foreground="#DDE7EF")
SUCCESS_STYLE = TextStyle(foreground="#5FD7AF", bold=True)
WARNING_STYLE = TextStyle(foreground="#FFB86B", bold=True)
FAILURE_STYLE = TextStyle(foreground="#FF6B6B")
COMMAND_STYLE = TextStyle(foreground="ansimagenta")

# 终端摘要文本只降低亮度，命令文本使用 ANSI 青色。
TERMINAL_DIM_STYLE   = TextStyle(dim=True)
TERMINAL_CYAN_STYLE  = TextStyle(foreground="ansicyan")
TERMINAL_TITLE_STYLE = TextStyle(bold=True)

ASSISTANT_PREFIX_CLASS = "class:assistant.prefix"

QUERY_PREFIX              = "› "
QUERY_CONTINUATION_PREFIX = "  "
QUERY_PREFIX_WIDTH        = 2
QUERY_RIGHT_MARGIN_WIDTH  = 1

TUI_APPLICATION_OVERRIDES = Style.from_dict({
    "assistant.prefix": "bold fg:#7F8C9A",
    "input-surface": "bg:default",
    "auto-suggestion": "bg:default #5A616A",
    "completion-menu": "bg:default #B8C0C9",
    "completion-menu.completion": "bg:default bold #B8C0C9",
    "completion-menu.completion.current": "bg:default bold ansiblue",
    "completion-menu.meta.completion": "bg:default #707A84",
    "completion-menu.meta.completion.current": "bg:default bold ansiblue",
    "completion-menu.empty": "dim italic #59616A",
    "completion-menu.empty.mention": "italic nodim #B8C0C9",
    "token-menu": "#B8C0C9",
    "token-menu.command": "fg:default",
    "token-menu.command.current": "bold nodim ansiblue",
    "token-menu.skill": "dim #B8C0C9",
    "token-menu.skill.current": "bold nodim ansiblue",
    "token-menu.skill-mention": "dim #B8C0C9",
    "token-menu.skill-mention.current": "bold nodim ansiblue",
    "token-menu.plugin-mention": "ansimagenta",
    "token-menu.plugin-mention.current": "bold nodim ansiblue",
    "token-menu.file-mention": "ansicyan",
    "token-menu.file-mention.current": "bold nodim ansiblue",
    "token-menu.directory-mention": "#B8C0C9",
    "token-menu.directory-mention.current": "bold nodim ansiblue",
    "token-menu.completion": "bold #B8C0C9",
    "token-menu.completion.current": "bold nodim ansiblue",
    "token-menu.meta.command": "fg:default dim",
    "token-menu.meta.command.current": "bold nodim ansiblue",
    "token-menu.meta.skill": "dim #7B838E",
    "token-menu.meta.skill.current": "bold nodim ansiblue",
    "token-menu.meta.skill-mention": "dim #7B838E",
    "token-menu.meta.skill-mention.current": "bold nodim ansiblue",
    "token-menu.meta.plugin-mention": "dim #7B838E",
    "token-menu.meta.plugin-mention.current": "bold nodim ansiblue",
    "token-menu.meta.file-mention": "dim #7B838E",
    "token-menu.meta.file-mention.current": "bold nodim ansiblue",
    "token-menu.meta.directory-mention": "dim #7B838E",
    "token-menu.meta.directory-mention.current": "bold nodim ansiblue",
    "token-menu.meta.completion": "dim #707A84",
    "token-menu.meta.completion.current": "bold nodim ansiblue",
    "tui-menu.footer.right.plugins.current": "bold nodim ansimagenta",
    "token-menu.hint": "bg:default #DDE7EF",
    "token-menu.hint.key": "bg:default #7B838E dim",
    "queue.label": "bg:default #8A929C",
    "queue.hint": "bg:default #7B838E dim",
    "queue.marker": "bg:default #7B838E dim",
    "queue.edit-hint": "bg:default #7B838E dim",
    "queue.text": "bg:default #DDE7EF dim",
    "queue.text.queued": "bg:default #DDE7EF dim italic",
    "queue.more": "bg:default #7B838E",
    "input.notice.marker": "bg:default #FF5F5F",
    "input.notice": "bg:default #FF8A8A",
    "input.notice.hint": "bg:default #DDE7EF",
    "input.notice.example": "bg:default #7F8C9A dim",
    "process-status.exec": "fg:#D8B26E",
    "process-status.background": "dim",
    "process-status.action": "ansiblue nodim",
    "directory-trust.title": "bold",
    "directory-trust.body": "",
    "directory-trust.warning": "ansiyellow",
    "directory-trust.option": "",
    "directory-trust.option.selected": "ansiblue",
    "directory-trust.error": "ansired",
    "directory-trust.hint": "dim",
    "directory-trust.key": "",
    "footer.separator": "fg:#7B838E",
    "footer.model": "fg:#F3F5F8",
    "footer.access": "fg:#8FC7EA",
    "footer.access.full": "fg:#D8B26E",
    "footer.workspace": "fg:#8A929C",
    "footer.mailbox": "fg:#8FC7EA bold",
    "footer.queue-hint": "fg:#7B838E dim",
    "footer.exit-key": "fg:#C9A86A",
    "footer.exit-hint": "fg:#8A929C",
    "shell.title.dot": "fg:#7F8C9A",
    "shell.title.dot.running": "fg:#7FB7F0 bold",
    "shell.title.dot.success": "fg:#8FD5A6 bold",
    "shell.title.dot.failure": "fg:#FF6B6B bold",
    "shell.title.action": "fg:ansiblue bold",
    "shell.title.command": "fg:#F4F7FA",
    "shell.title.suffix": "fg:#7F8C9A",
    "shell.status": "fg:#87919D",
    "shell.stdout": "fg:#D8DCE2",
    "shell.stderr": "fg:#B8C1CB",
    "ps.title": "bold",
    "ps.separator": "fg:#69727D",
    "ps.meta": "italic",
    "ps.warning": "fg:#FFB86B bold",
    "ps.command": "fg:#D8DCE2",
    "ps.help": "fg:#69727D",
    "ps.output": "dim",
    "ps.stream": "dim",
    "ps.stream.command": "fg:ansicyan nodim",
    "ps.waiting": "dim",
    "ps.error": "fg:#FF6B6B",
    "scrollback.history-notice": "fg:#87919D dim",
    "transcript.overlay.title": "fg:#87919D dim",
    "transcript.overlay.rule": "fg:#69727D dim",
    "transcript.overlay.help": "fg:#87919D",
    "transcript.overlay.progress": "fg:#DDE7EF bold",
    "transcript.overlay.filler": "fg:#69727D dim",
    "transcript.overlay.selection": "bg:#1D3969 fg:#F4F7FA",
    "transcript.overlay.search-match": "bg:#1D3969 fg:#F4F7FA",
    "transcript.overlay.search-prompt": "fg:#8FC7EA bold",
    "transcript.overlay.search-query": "fg:#F4F7FA",
    "transcript.overlay.search-cursor": "fg:#8FC7EA",
    "transcript.overlay.export-success": "fg:#8FD5A6",
    "transcript.overlay.export-error": "fg:#FF8A8A",
    "static-pager.title": "fg:#87919D dim",
    "static-pager.rule": "fg:#69727D dim",
    "static-pager.help": "fg:#87919D",
    "static-pager.progress": "fg:#DDE7EF bold",
    "static-pager.empty": "fg:#87919D dim italic",
    "static-pager.filler": "fg:#69727D dim",
    "approval-patch-action": "bold",
    "approval-patch-path": "",
    "approval-patch-count-add": "ansigreen",
    "approval-patch-count-remove": "ansired",
    "approval-patch-context": "",
    "mailbox.title": "fg:#87919D dim",
    "mailbox.rule": "fg:#69727D dim",
    "mailbox.status": "fg:#DDE7EF bold",
    "mailbox.subject": "fg:#F4F7FA bold",
    "mailbox.detail": "fg:#87919D dim",
    "mailbox.message": "fg:#DDE7EF",
    "mailbox.empty": "fg:#DDE7EF",
    "mailbox.help": "fg:#87919D",
    "mailbox.progress": "fg:#DDE7EF bold",
    "mailbox.filler": "fg:#69727D dim",
    "resume-picker.title": "bold ansicyan",
    "resume-picker.rule": "fg:#69727D dim",
    "resume-picker.search": "fg:#F4F7FA",
    "resume-picker.search.placeholder": "fg:#87919D dim",
    "resume-picker.toolbar": "fg:#87919D dim",
    "resume-picker.toolbar.active": "fg:#DDE7EF nodim",
    "resume-picker.toolbar.focused": "ansimagenta nodim",
    "resume-picker.marker": "ansiyellow bold",
    "resume-picker.title.selected": "ansiyellow",
    "resume-picker.meta": "fg:#87919D dim",
    "resume-picker.meta.placeholder": "fg:#87919D dim italic",
    "resume-picker.row.selected": "",
    "resume-picker.row.zebra": "",
    "resume-picker.empty": "fg:#87919D dim italic",
    "resume-picker.error": "fg:#FF6B6B italic",
    "resume-picker.help": "fg:#87919D dim",
    "resume-picker.help.key": "fg:#DDE7EF nodim",
    "resume-picker.progress": "fg:#DDE7EF bold",
    "resume-picker.preview.user": "fg:#B8C0C9 italic",
    "resume-picker.preview.assistant": "fg:#707A84",
})


def _surface_style(capabilities: TerminalCapabilities) -> BaseStyle:
    """根据终端主题创建动态表面和前景样式。"""
    terminal_background = capabilities.theme.background
    if not capabilities.dynamic_surfaces or terminal_background is None:
        return Style.from_dict({})

    light   = _is_light_color(terminal_background)
    overlay = (0, 0, 0) if light else (255, 255, 255)

    surface_background = (
        _blend_color(overlay, terminal_background, 0.04)
        if light
        else _blend_color(overlay, terminal_background, 0.12)
    )
    selected_background = _surface_color(
        _blend_color(overlay, terminal_background, 0.12),
        capabilities,
    )
    zebra_background = _surface_color(_blend_color(
        overlay,
        terminal_background,
        0.04 if light else 0.055,
    ), capabilities)

    background = f"bg:{_surface_color(surface_background, capabilities)}"

    styles = {
        "input-surface": background,
        "approval-card": background,
        "menu-card": background,
        "resume-picker.row.selected": f"bg:{selected_background}",
        "resume-picker.row.zebra": f"bg:{zebra_background}",
    }

    if light:
        accent = semantic_color(
            (0, 95, 135),
            capabilities.color_level,
            fallback="ansicyan",
        )
        styles.update({
            "prompt": "#20262C",
            "prompt.kicker": "bold #596570",
            "prompt.command.slash": "ansimagenta",
            "footer.model": "#005F87",
            "placeholder": "#68737D",
            "auto-suggestion": "#737F89",
            "completion-menu.empty": "dim italic #68737D",
            "completion-menu.empty.mention": "italic nodim #52606C",
            "completion-menu.completion.current": "bold #005F87",
            "completion-menu.meta.completion.current": "bold #005F87",
            "token-menu": "#52606C",
            "token-menu.command": "fg:default",
            "token-menu.command.current": "bold nodim #005F87",
            "token-menu.skill": "dim #52606C",
            "token-menu.skill.current": "bold nodim #005F87",
            "token-menu.skill-mention": "dim #52606C",
            "token-menu.skill-mention.current": "bold nodim #005F87",
            "token-menu.plugin-mention": "ansimagenta",
            "token-menu.plugin-mention.current": "bold nodim #005F87",
            "token-menu.file-mention": "#005F87",
            "token-menu.file-mention.current": "bold nodim #005F87",
            "token-menu.directory-mention": "#52606C",
            "token-menu.directory-mention.current": "bold nodim #005F87",
            "token-menu.completion": "bold #52606C",
            "token-menu.completion.current": "bold nodim #005F87",
            "token-menu.meta.command": "fg:default dim",
            "token-menu.meta.command.current": "bold nodim #005F87",
            "token-menu.meta.skill": "dim #68737D",
            "token-menu.meta.skill.current": "bold nodim #005F87",
            "token-menu.meta.skill-mention": "dim #68737D",
            "token-menu.meta.skill-mention.current": "bold nodim #005F87",
            "token-menu.meta.plugin-mention": "dim #68737D",
            "token-menu.meta.plugin-mention.current": "bold nodim #005F87",
            "token-menu.meta.file-mention": "dim #68737D",
            "token-menu.meta.file-mention.current": "bold nodim #005F87",
            "token-menu.meta.directory-mention": "dim #68737D",
            "token-menu.meta.directory-mention.current": "bold nodim #005F87",
            "token-menu.meta.completion": "#68737D",
            "token-menu.meta.completion.current": "bold nodim #005F87",
            "tui-menu.footer.right.plugins.current": "bold nodim ansimagenta",
            "token-menu.hint": "#20262C",
            "token-menu.hint.key": "#68737D dim",
            "approval-question": "bold",
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
            "approval-patch-action": "bold",
            "approval-patch-path": "",
            "approval-patch-count-add": "ansigreen",
            "approval-patch-count-remove": "ansired",
            "approval-patch-context": "",
            "tui-menu.tab-selected": "bold #005F87",
            "tui-menu.title.current": "#005F87",
            "tui-menu.index.active": "bold #005F87",
            "tui-menu.label.active": "bold #005F87",
            "tui-menu.detail-selected": "bold #005F87",
            "resume-picker.title": "bold #006400",
            "resume-picker.rule": "#68737D dim",
            "resume-picker.search": "#20262C",
            "resume-picker.search.placeholder": "#68737D dim",
            "resume-picker.toolbar": "#68737D dim",
            "resume-picker.toolbar.active": "#20262C nodim",
            "resume-picker.marker": "ansimagenta bold",
            "resume-picker.title.selected": "ansimagenta",
            "resume-picker.meta": "#68737D dim",
            "resume-picker.meta.placeholder": "#68737D dim italic",
            "resume-picker.empty": "#68737D dim italic",
            "resume-picker.error": "#B42318 italic",
            "resume-picker.help": "#68737D dim",
            "resume-picker.help.key": "#20262C nodim",
            "resume-picker.progress": "#20262C bold",
            "resume-picker.preview.user": "#596570 italic",
            "resume-picker.preview.assistant": "#68737D",
        })
        for name, value in tuple(styles.items()):
            if "#005F87" in value:
                styles[name] = value.replace("#005F87", accent)
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


def _surface_color(
    color: RgbColor,
    capabilities: TerminalCapabilities,
) -> str:
    """将表面 RGB 按终端色阶转换为可渲染颜色。"""
    return best_color(color, capabilities.color_level) or "default"


def _is_light_color(color: RgbColor) -> bool:
    """根据相对亮度判断 RGB 是否属于亮色主题。"""
    return is_light_color(color)


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
            (style.strikethrough, "strike"),
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
        _terminal_semantic_style(capabilities),
    ])


def _terminal_semantic_style(capabilities: TerminalCapabilities) -> BaseStyle:
    """根据终端前景/背景和色阶覆盖高频语义颜色。"""
    styles: dict[str, str] = {
        # 信息栏分隔符使用终端默认前景，避免把表格的低对比度 RGB 带入 footer。
        "footer.separator": "fg:default dim",
    }
    foreground = capabilities.theme.foreground
    background = capabilities.theme.background

    if foreground is not None and background is not None:
        separator = best_color(
            _blend_color(foreground, background, 0.20),
            capabilities.color_level,
        )

        separator_style = f"fg:{separator}" if separator else "dim"

        for style_class in (
            "ps.separator",
            "transcript.overlay.rule",
            "mailbox.rule",
            "resume-picker.rule",
        ):
            styles[style_class] = separator_style

    light = background is not None and _is_light_color(background)

    accent = (
        semantic_color(
            (0, 95, 135),
            capabilities.color_level,
            fallback="ansicyan",
        )
        if light
        else "ansicyan"
    )

    for style_class in ("footer.model",):
        styles[style_class] = f"fg:{accent} bold"

    selection = selection_color(
        capabilities.color_level,
        light=light,
    )

    styles["shell.title.action"] = f"fg:{selection} bold"
    styles["process-status.action"] = f"fg:{selection} nodim"

    selection_background = best_color(
        (207, 225, 246) if light else (29, 57, 105),
        capabilities.color_level,
    ) or "ansiblue"

    selection_foreground = "#20262C" if light else "#F4F7FA"

    for style_class in (
        "approval-option-selected",
        "tui-menu.index.active",
        "tui-menu.label.active",
        "tui-menu.detail-selected",
        "tui-menu.title.current",
        "tui-menu.status.current",
        "tui-menu.tab-selected",
        "tui-menu.footer.right.current",
        "completion-menu.completion.current",
        "completion-menu.meta.completion.current",
        "token-menu.command.current",
        "token-menu.skill.current",
        "token-menu.skill-mention.current",
        "token-menu.plugin-mention.current",
        "token-menu.file-mention.current",
        "token-menu.directory-mention.current",
        "token-menu.completion.current",
        "token-menu.meta.command.current",
        "token-menu.meta.skill.current",
        "token-menu.meta.skill-mention.current",
        "token-menu.meta.plugin-mention.current",
        "token-menu.meta.file-mention.current",
        "token-menu.meta.directory-mention.current",
        "token-menu.meta.completion.current",
        "directory-trust.option.selected",
    ):
        styles[style_class] = f"fg:{selection} bold"
    for style_class in (
        "transcript.overlay.selection",
        "transcript.overlay.search-match",
    ):
        styles[style_class] = (
            f"bg:{selection_background} fg:{selection_foreground}"
        )

    return Style.from_dict(styles)


def exit_summary_fragments(session_id: str) -> tuple[tuple[str, str], ...]:
    """生成 TUI 释放终端后的会话恢复提示。"""
    command = f"{const.APP_NAME} resume {session_id}"

    return (
        ("dim fg:#7F8C9A", "■ "),
        ("fg:#DDE7EF", "To continue this session, run "),
        ("fg:#8FB8FF", command),
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
        if hyperlinks:
            style = terminal_hyperlink_style(style, span.hyperlink)
        fragments.append((style, span.text))

    return tuple(fragments)


def styled_fragment_block(
    block: StyledBlock,
    *,
    fallback_style: TextStyle | None = None,
    hyperlinks: bool = False
) -> FragmentBlock:
    """把中立样式块连同行级填充规则转换为 TUI 片段块。"""
    return FragmentBlock(
        styled_block_fragments(
            block,
            fallback_style=fallback_style,
            hyperlinks=hyperlinks,
        ),
        line_fills=tuple(
            LineFill(character=" ", style=prompt_style(style))
            if style is not None
            else None
            for style in block.line_fill_styles
        ),
    )


def assistant_block(block: FragmentBlock) -> FragmentBlock:
    """给助手正文添加项目符号和显式续行缩进。"""
    return FragmentBlock(assistant_fragments(block.fragments))


def assistant_continuation_block(block: FragmentBlock) -> FragmentBlock:
    """给助手正文续块添加与正文对齐的显式缩进。"""
    fragments = assistant_fragments(block.fragments)
    if not fragments:
        return FragmentBlock(())
    return FragmentBlock((
        (ASSISTANT_PREFIX_CLASS, "  "),
        *fragments[1:],
    ))


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

    prefixed = (
        (ASSISTANT_PREFIX_CLASS, "• "),
        *_assistant_continuation_fragments(
            out,
            indent_style=ASSISTANT_PREFIX_CLASS,
        ),
    )
    return _normalize_whitespace_only_fragments(prefixed)


def _normalize_whitespace_only_fragments(
    fragments: tuple[tuple[str, str], ...]
) -> tuple[tuple[str, str], ...]:
    """把只含空白和终端链接控制符的显示行转换为空行。"""
    lines = split_formatted_lines(list(fragments))

    normalized = []

    for line in lines:
        visible = "".join(
            text
            for style, text in line
            if ZERO_WIDTH_ESCAPE_STYLE not in style
        )
        normalized.append([] if not visible.strip() else line)

    return tuple(join_formatted_lines(normalized))


def _assistant_continuation_fragments(
    fragments: list[tuple[str, str]],
    *,
    indent_style: str
) -> tuple[tuple[str, str], ...]:
    """在助手正文每个显式续行前补充两个空格。"""
    out: list[tuple[str, str]] = []

    continuation: bool = False

    for style, text in fragments:
        lines      = text.split("\n")
        last_index = len(lines) - 1

        for index, line in enumerate(lines):
            has_newline = index < last_index
            if continuation and line:
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


def failure_parts(
    text: str,
    style: TextStyle = FAILURE_STYLE
) -> tuple[TextSpan, TextSpan]:
    """生成带非粗体方块标记的错误片段。"""
    return (
        TextSpan("■", FAILURE_STYLE),
        TextSpan(f" {text}", style),
    )


def failure_text_block(
    text: str,
    style: TextStyle = FAILURE_STYLE
) -> FragmentBlock:
    """生成带非粗体方块标记的独立错误块。"""
    return fragment_block(*failure_parts(text, style))


def query_block(
    text: str,
    *,
    command_aware: bool = True
) -> FragmentBlock:
    """按普通 query 或命令类型生成用户输入块。"""
    value = str(text).strip() if command_aware else str(text)
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    slash_command = bool(
        command_aware and resolve_slash_command(value) is not None
    )

    command = bool(
        command_aware
        and (slash_command or value.startswith(("!", "$", "\\")))
    )

    text_style = "class:prompt.command.slash" if slash_command else "class:prompt"

    fragments: list[tuple[str, str]] = []

    for index, line in enumerate(lines):
        if index:
            fragments.append(("", "\n"))
        marker = "" if command else ("› " if index == 0 else "  ")
        if marker:
            fragments.append(("class:prompt.kicker", marker))
        fragments.append((text_style, line))

    return FragmentBlock(tuple(fragments))


def query_display_block(
    text: str,
    terminal_width: int
) -> FragmentBlock:
    """按终端宽度生成带续行缩进的用户 query 显示块。"""
    width = max(1, int(terminal_width))
    value = (
        str(text)
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .rstrip("\n")
    )
    lines = value.split("\n")

    body = join_formatted_lines([
        [("class:prompt", line)]
        for line in lines
    ])

    safe_body = sanitize_fragment_block(FragmentBlock(tuple(body)))

    wrapped = wrap_formatted_lines(
        list(safe_body.fragments),
        width=max(
            1,
            width - QUERY_PREFIX_WIDTH - QUERY_RIGHT_MARGIN_WIDTH,
        ),
    )
    if not wrapped:
        return FragmentBlock(())

    rows: list[list[tuple[str, str]]] = []
    for index, row in enumerate(wrapped):
        rows.append([
            (
                "class:prompt.kicker",
                QUERY_PREFIX if index == 0 else QUERY_CONTINUATION_PREFIX,
            ),
            *row,
        ])

    return FragmentBlock(tuple(join_formatted_lines(rows)))


def query_preview_block(
    text: str,
    terminal_width: int,
    *,
    transcript_key: str,
    max_rows: int = 8
) -> FragmentBlock:
    """生成保留完整记录入口的宽度感知用户输入预览。"""
    width = max(1, int(terminal_width))
    limit = max(2, int(max_rows))
    block = query_display_block(text, width)

    rows = split_formatted_lines(list(block.fragments))
    if len(rows) <= limit:
        return block

    retained = limit - 1
    omitted  = len(rows) - retained
    marker   = f"… +{omitted} lines"

    hint = transcript_hint(
        marker,
        transcript_key,
        width,
        prefix="  ",
    )
    omitted_row = clip_fragments([
        ("class:prompt.kicker", f"  {marker}{hint}"),
    ], width=width)

    return FragmentBlock(tuple(join_formatted_lines([
        *rows[:retained],
        omitted_row,
    ])))


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


def interrupted_status_block(
    label: str,
    *,
    action: str = ""
) -> FragmentBlock:
    """生成带中性项目符号的前台操作中断状态。"""
    detail = f"{action} interrupted" if action else "interrupted"
    return fragment_block(
        TextSpan("• ", MUTED_STYLE),
        TextSpan(f"{label} ", ACCENT_STYLE),
        TextSpan(f"· {detail}", WARNING_STYLE),
    )


if __name__ == '__main__':
    pass
