# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from prompt_toolkit.styles import (
    Attrs,
    BaseStyle,
    DummyStyleTransformation,
    Style,
    StyleTransformation,
    merge_styles,
)

from agent.ports.presentation import (
    StyledBlock,
    TextSpan,
    TextStyle,
)
from frontends.terminal.capabilities import (
    DEGRADED_TERMINAL_CAPABILITIES,
    TerminalCapabilities,
)
from frontends.terminal.semantic_styles import (
    TerminalSemanticRole,
    TerminalSemanticStyles,
    TerminalStyle,
    resolve_terminal_semantic_styles,
    semantic_role_for_ansi_color,
    semantic_text_style,
)
from metadata import const
from .hyperlinks import terminal_hyperlink_style
from .models import (
    FragmentBlock,
    LineFill,
)
from ..prompting.commands import (
    canonical_command_label,
    resolve_slash_command
)
from ..rendering.fragments import (
    ZERO_WIDTH_ESCAPE_STYLE,
    clip_fragments,
    join_formatted_lines,
    split_formatted_lines,
    transcript_hint,
    wrap_formatted_lines
)
from ..rendering.text_sanitize import sanitize_fragment_block

MUTED_STYLE = semantic_text_style(TerminalSemanticRole.SECONDARY)
ACCENT_STYLE = semantic_text_style(TerminalSemanticRole.ACCENT, bold=True)
BRIGHT_STYLE = semantic_text_style(TerminalSemanticRole.PRIMARY, bold=True)
BODY_STYLE = semantic_text_style(TerminalSemanticRole.PRIMARY)
SUCCESS_STYLE = semantic_text_style(TerminalSemanticRole.SUCCESS, bold=True)
WARNING_STYLE = semantic_text_style(TerminalSemanticRole.ATTENTION, bold=True)
FAILURE_STYLE = semantic_text_style(TerminalSemanticRole.FAILURE)
COMMAND_STYLE = semantic_text_style(TerminalSemanticRole.BRAND)

# 终端摘要文本只降低亮度，命令文本使用 ANSI 青色。
TERMINAL_DIM_STYLE = TextStyle(dim=True)
TERMINAL_CYAN_STYLE = semantic_text_style(TerminalSemanticRole.ACCENT)
TERMINAL_TITLE_STYLE = TextStyle(bold=True)

ASSISTANT_PREFIX_CLASS = "class:assistant.prefix"

QUERY_PREFIX = "› "
QUERY_CONTINUATION_PREFIX = "  "
QUERY_PREFIX_WIDTH = 2
QUERY_RIGHT_MARGIN_WIDTH = 1

TUI_APPLICATION_OVERRIDES = Style.from_dict({
    "assistant.prefix": "bold",
    "input-surface": "bg:default",
    "menu-card": "",
    "queue.hint": "dim",
    "queue.marker": "dim",
    "queue.edit-hint": "dim",
    "queue.text": "dim",
    "queue.text.queued": "dim italic",
    "input.notice.example": "dim",
    "process-status.background": "dim",
    "process-status.action": "nodim",
    "directory-trust.title": "bold",
    "directory-trust.hint": "dim",
    "footer.mailbox": "bold",
    "footer.queue-hint": "dim",
    "shell.title.dot.running": "bold",
    "shell.title.dot.success": "bold",
    "shell.title.dot.failure": "bold",
    "shell.title.action": "bold",
    "ps.title": "bold",
    "ps.meta": "italic",
    "ps.warning": "bold",
    "ps.output": "dim",
    "ps.stream": "dim",
    "ps.stream.command": "nodim",
    "ps.waiting": "dim",
    "scrollback.history-notice": "dim",
    "transcript.overlay.title": "dim",
    "transcript.overlay.rule": "dim",
    "transcript.overlay.progress": "bold",
    "transcript.overlay.filler": "dim",
    "static-pager.title": "dim",
    "static-pager.rule": "dim",
    "static-pager.progress": "bold",
    "static-pager.empty": "dim italic",
    "static-pager.filler": "dim",
    "mailbox.title": "dim",
    "mailbox.status": "bold",
    "mailbox.subject": "bold",
    "mailbox.detail": "dim",
    "mailbox.progress": "bold",
    "mailbox.filler": "dim",
    "resume-picker.title": "bold",
    "resume-picker.rule": "dim",
    "resume-picker.search.placeholder": "dim",
    "resume-picker.toolbar": "dim",
    "resume-picker.toolbar.active": "nodim",
    "resume-picker.toolbar.focused": "nodim",
    "resume-picker.marker": "bold",
    "resume-picker.meta": "dim",
    "resume-picker.meta.placeholder": "dim italic",
    "resume-picker.row.selected": "",
    "resume-picker.row.zebra": "",
    "resume-picker.empty": "dim italic",
    "resume-picker.error": "italic",
    "resume-picker.help": "dim",
    "resume-picker.help.key": "nodim",
    "resume-picker.progress": "bold",
    "resume-picker.preview.user": "italic",
})


class _BackgroundlessStyleTransformation(StyleTransformation):
    """在当前 Application 输出边界移除背景，覆盖组件和内联片段样式。"""

    def transform_attrs(self, attrs: Attrs) -> Attrs:
        """保留文字语义并把反色强调转换为加粗。"""
        # 显式 default 仍会被 renderer 判为有样式并填充行尾，须归一为空值。
        color = "" if attrs.color in {"default", "ansidefault"} else attrs.color
        return attrs._replace(
            color=color,
            bgcolor="",
            reverse=False,
            bold=attrs.bold or attrs.reverse,
        )


def _surface_style(semantics: TerminalSemanticStyles) -> BaseStyle:
    """根据已解析语义 token 创建动态表面。"""

    surface_background = semantics.user_surface.background
    approval_background = semantics.approval_surface.background
    selected_background = semantics.selected_surface.background
    zebra_background = semantics.zebra_surface.background
    if (
        surface_background is None
        or approval_background is None
        or selected_background is None
        or zebra_background is None
    ):
        return Style.from_dict({})

    background = f"bg:{surface_background}"

    styles = {
        "input-surface": background,
        "approval-card": f"bg:{approval_background}",
        "menu-card": background,
        "resume-picker.row.selected": f"bg:{selected_background}",
        "resume-picker.row.zebra": f"bg:{zebra_background}",
    }

    return Style.from_dict(styles)


def _terminal_semantic_style(semantics: TerminalSemanticStyles) -> BaseStyle:
    """把共享语义 token 投影到全部普通 TUI 样式类。"""

    separator_style = _terminal_text_style(semantics.separator)
    styles: dict[str, str] = {
        "terminal.primary": _terminal_text_style(semantics.primary),
        "terminal.secondary": _terminal_text_style(semantics.secondary),
        "terminal.accent": _terminal_text_style(semantics.accent_plain),
        "terminal.selected": _terminal_text_style(semantics.selected),
        "terminal.success": _terminal_text_style(semantics.success),
        "terminal.failure": _terminal_text_style(semantics.failure),
        "terminal.attention": _terminal_text_style(semantics.attention),
        "terminal.attention.plain": _terminal_text_style(TerminalStyle(
            foreground=semantics.attention.foreground,
        )),
        "terminal.brand": _terminal_text_style(semantics.brand),
    }

    role_classes = (
        (
            semantics.primary,
            (
                "prompt",
                "prompt.model",
                "completion-menu",
                "completion-menu.completion",
                "completion-menu.empty.mention",
                "token-menu",
                "token-menu.command",
                "token-menu.completion",
                "token-menu.hint",
                "queue.text",
                "queue.text.queued",
                "input.notice.hint",
                "directory-trust.title",
                "directory-trust.body",
                "directory-trust.option",
                "directory-trust.key",
                "shell.title.command",
                "shell.stdout",
                "ps.title",
                "ps.command",
                "transcript.overlay.progress",
                "static-pager.progress",
                "approval-question",
                "approval-field-label",
                "approval-field-value",
                "approval-permission-label",
                "approval-permission-value",
                "approval-patch-action",
                "approval-patch-path",
                "approval-patch-context",
                "approval-command",
                "approval-command-path",
                "approval-mcp-value",
                "mailbox.status",
                "mailbox.subject",
                "mailbox.message",
                "mailbox.empty",
                "mailbox.progress",
                "resume-picker.search",
                "resume-picker.toolbar.active",
                "resume-picker.help.key",
                "resume-picker.progress",
                "resume-picker.preview.user",
                "tui-menu.title",
                "tui-menu.search",
                "tui-menu.label",
                "tui-menu.body",
                "tui-menu.body.heading",
                "tui-menu.footer.hint",
            ),
        ),
        (
            semantics.secondary,
            (
                "assistant.prefix",
                "prompt.kicker",
                "prompt.muted",
                "prompt.workspace",
                "prompt.exec.command",
                "placeholder",
                "auto-suggestion",
                "completion-menu.meta.completion",
                "completion-menu.empty",
                "token-menu.skill",
                "token-menu.skill-mention",
                "token-menu.directory-mention",
                "token-menu.meta.command",
                "token-menu.meta.skill",
                "token-menu.meta.skill-mention",
                "token-menu.meta.plugin-mention",
                "token-menu.meta.file-mention",
                "token-menu.meta.directory-mention",
                "token-menu.meta.completion",
                "token-menu.hint.key",
                "queue.label",
                "queue.hint",
                "queue.marker",
                "queue.edit-hint",
                "queue.more",
                "input.notice.example",
                "process-status.background",
                "directory-trust.hint",
                "footer.workspace",
                "footer.queue-hint",
                "footer.exit-hint",
                "shell.title.dot",
                "shell.title.suffix",
                "shell.status",
                "shell.stderr",
                "ps.help",
                "ps.output",
                "ps.stream",
                "ps.waiting",
                "scrollback.history-notice",
                "transcript.overlay.title",
                "transcript.overlay.help",
                "transcript.overlay.filler",
                "static-pager.title",
                "static-pager.help",
                "static-pager.empty",
                "static-pager.filler",
                "approval-context",
                "approval-meta",
                "approval-omitted",
                "approval-footer",
                "approval-option",
                "approval-shortcut",
                "approval-command-operator",
                "approval-mcp-unknown",
                "mailbox.title",
                "mailbox.detail",
                "mailbox.help",
                "mailbox.filler",
                "resume-picker.search.placeholder",
                "resume-picker.toolbar",
                "resume-picker.meta",
                "resume-picker.meta.placeholder",
                "resume-picker.empty",
                "resume-picker.help",
                "resume-picker.preview.assistant",
                "tui-menu.status",
                "tui-menu.help",
                "tui-menu.search.placeholder",
                "tui-menu.search.empty",
                "tui-menu.tab",
                "tui-menu.index",
                "tui-menu.detail",
                "tui-menu.body.empty",
                "tui-menu.label.disabled",
                "tui-menu.detail.disabled",
                "tui-menu.index.disabled",
                "tui-menu.footer",
                "tui-menu.footer.note",
                "tui-menu.footer.key",
                "tui-menu.footer.right",
                "tui-menu.category",
            ),
        ),
        (
            semantics.accent_plain,
            (
                "prompt.access",
                "prompt.exec",
                "skill-token",
                "token-menu.file-mention",
                "footer.access",
                "footer.mailbox",
                "footer.model",
                "footer.raw",
                "tui-menu.input-gutter",
                "shell.title.dot.running",
                "ps.stream.command",
                "approval-network",
                "approval-network-host",
                "approval-permission-rule",
                "approval-command-head",
                "approval-command-flag",
                "approval-mcp-label",
                "approval-mcp-connector",
                "resume-picker.title",
            ),
        ),
        (
            semantics.success,
            (
                "shell.title.dot.success",
                "approval-patch-count-add",
                "approval-command-string",
                "approval-mcp-readonly",
            ),
        ),
        (
            semantics.failure,
            (
                "shell-escape",
                "input.notice.marker",
                "input.notice",
                "directory-trust.error",
                "shell.title.dot.failure",
                "ps.error",
                "approval-patch-count-remove",
                "approval-mcp-destructive",
                "resume-picker.error",
                "tui-menu.warning",
                "tui-menu.error",
            ),
        ),
        (
            semantics.attention,
            (
                "prompt.access.full",
                "paste-placeholder",
                "process-status.exec",
                "directory-trust.warning",
                "footer.access.full",
                "footer.exit-key",
                "ps.warning",
                "approval-command-number",
                "approval-mcp-write",
                "tui-menu.review",
            ),
        ),
        (
            semantics.brand,
            (
                "prompt.command.slash",
                "token-menu.plugin-mention",
                "tui-menu.footer.right.plugins.current",
                "footer.brand",
                "resume-picker.toolbar.focused",
            ),
        ),
        (
            semantics.selected,
            (
                "process-status.action",
                "shell.title.action",
                "approval-option-selected",
                "approval-shortcut-selected",
                "directory-trust.option.selected",
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
                "resume-picker.marker",
                "resume-picker.title.selected",
                "tui-menu.title.current",
                "tui-menu.status.current",
                "tui-menu.tab-selected",
                "tui-menu.index.active",
                "tui-menu.label.active",
                "tui-menu.review-selected",
                "tui-menu.detail-selected",
                "tui-menu.selection-marker",
                "tui-menu.footer.right.current",
            ),
        ),
    )
    for role_style, style_classes in role_classes:
        value = _terminal_text_style(role_style)
        for style_class in style_classes:
            styles[style_class] = value

    for style_class in (
        "footer.separator",
        "ps.separator",
        "transcript.overlay.rule",
        "static-pager.rule",
        "mailbox.rule",
        "resume-picker.rule",
    ):
        styles[style_class] = separator_style
    for style_class in ("transcript.overlay.selection",):
        styles[style_class] = _terminal_surface_style(semantics.selected_surface)

    styles["tui-menu.title"] = _terminal_text_style(
        semantics.primary,
        "bold",
    )
    styles["tui-menu.footer.secondary"] = _terminal_text_style(
        semantics.secondary,
    )
    styles["tui-menu.selection-marker"] = _terminal_text_style(
        semantics.selected,
        "nodim",
    )

    return Style.from_dict(styles)


def _terminal_text_style(style: TerminalStyle, *modifiers: str) -> str:
    """把终端文本 token 转换为 prompt_toolkit 样式。"""

    parts = [f"fg:{style.foreground or 'default'}"]
    parts.extend(
        name
        for enabled, name in (
            (style.bold, "bold"),
            (style.dim, "dim"),
            (style.reverse, "reverse"),
        )
        if enabled
    )
    parts.extend(modifiers)
    return " ".join(parts)


def _terminal_surface_style(style: TerminalStyle) -> str:
    """把终端表面 token 转换为 prompt_toolkit 样式。"""

    if style.background is not None:
        return f"bg:{style.background} fg:default"
    return "bg:default fg:default reverse" if style.reverse else "bg:default fg:default"


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
        lines = text.split("\n")
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


def prompt_style(style: TextStyle) -> str:
    """把中立文本样式转换为 prompt_toolkit 样式字符串。"""
    role = semantic_role_for_ansi_color(style.foreground)
    role_class = (
        "terminal.attention.plain"
        if role is TerminalSemanticRole.ATTENTION
        else f"terminal.{role.value}" if role is not None else ""
    )
    parts = [f"class:{role_class}"] if role_class else []
    parts.extend(
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
    )

    if style.foreground and role is None:
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

    semantics = resolve_terminal_semantic_styles(
        capabilities.color_support,
        foreground=capabilities.theme.foreground,
        background=capabilities.theme.background,
    )
    styles = [
        input_style,
        approval_style,
        menu_style,
        TUI_APPLICATION_OVERRIDES,
        _surface_style(semantics),
        _terminal_semantic_style(semantics),
    ]
    if capabilities.identity.is_ide_terminal:
        styles.append(Style.from_dict({
            "transcript.overlay.selection": "bold underline",
        }))
    return merge_styles(styles)


def build_tui_style_transformation(
    capabilities: TerminalCapabilities,
) -> StyleTransformation:
    """为会话冻结 IDE 背景策略，供画布和原生滚屏输出共同使用。"""
    if capabilities.identity.is_ide_terminal:
        return _BackgroundlessStyleTransformation()
    return DummyStyleTransformation()


def exit_summary_fragments(session_id: str) -> tuple[tuple[str, str], ...]:
    """生成 TUI 释放终端后的会话恢复提示。"""
    command = f"{const.APP_NAME} resume {session_id}"

    return (
        (prompt_style(MUTED_STYLE), "■ "),
        (prompt_style(BODY_STYLE), "To continue this session, run "),
        (prompt_style(TERMINAL_CYAN_STYLE), command),
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


def info_text_block(text: str) -> FragmentBlock:
    """生成带中性项目符号的独立信息块。"""
    return fragment_block(
        TextSpan("• ", BODY_STYLE),
        TextSpan(str(text), BRIGHT_STYLE),
    )


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
    omitted = len(rows) - retained
    marker = f"… +{omitted} lines"

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
