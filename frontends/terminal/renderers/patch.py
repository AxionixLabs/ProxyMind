# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass, replace

from agent.application.views import (
    PatchFileView,
    PatchLineView,
    PatchView,
)
from agent.ports.presentation import (
    StyledBlock,
    TextSpan,
    TextStyle,
)
from frontends.terminal.capabilities import TerminalCapabilities
from frontends.terminal.color_support import TerminalColorLevel
from frontends.terminal.highlighting import highlight_code_lines
from frontends.terminal.palette import best_color, is_light_color
from frontends.terminal.probe import RgbColor
from frontends.terminal.text_layout import (
    wrap_styled_line,
    wrap_styled_lines
)

PATCH_TITLE_STYLE = TextStyle(bold=True)
PATCH_MUTED_STYLE = TextStyle(dim=True)

# prompt_toolkit 将这些 RGB 值在 ANSI-256 输出下精确量化。
_DARK_ANSI256_ADD_BG = "#005F00"  # 22
_DARK_ANSI256_REMOVE_BG = "#5F0000"  # 52
_LIGHT_ANSI256_ADD_BG = "#D7FFD7"  # 194
_LIGHT_ANSI256_REMOVE_BG = "#FFD7D7"  # 224
_LIGHT_ANSI256_ADD_GUTTER_BG = "#AFFFAF"  # 157
_LIGHT_ANSI256_REMOVE_GUTTER_BG = "#FFAFAF"  # 217
_LIGHT_ANSI256_GUTTER_FG = "#303030"  # 236


@dataclass(frozen=True, slots=True)
class DiffRenderStyleContext(object):
    """保存一次 patch 渲染所需的全部已解析样式。"""

    light: bool
    rich: bool
    add_foreground: str | None
    remove_foreground: str | None
    failure_foreground: str | None
    add_background: str | None
    remove_background: str | None
    add_gutter_background: str | None
    remove_gutter_background: str | None
    gutter_foreground: str | None


def render_patch_view(
    view: PatchView,
    *,
    style_context: DiffRenderStyleContext,
    terminal_width: int | None = None,
    measure_width: typing.Callable[[str], int] | None = None,
) -> StyledBlock:
    """把结构化补丁视图转换为终端展示块。"""
    if view.phase == "failed":
        spans = _failure_spans(
            view,
            context=style_context,
            terminal_width=terminal_width,
            measure_width=measure_width,
        )
    else:
        spans = _success_spans(
            view,
            context=style_context,
            terminal_width=terminal_width,
            measure_width=measure_width,
        )

    return StyledBlock(
        plain_text="".join(span.text for span in spans),
        spans=tuple(spans),
        line_fill_styles=_line_fill_styles(spans),
        preserve_spans=True,
    )


def _success_spans(
    view: PatchView,
    *,
    context: DiffRenderStyleContext,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None
) -> list[TextSpan]:
    """生成补丁的完整标题和差异正文。"""
    files = sorted(view.files, key=_file_sort_path)
    added = sum(file.added for file in files)
    removed = sum(file.removed for file in files)

    bullet_style = PATCH_MUTED_STYLE

    if len(files) == 1:
        file = files[0]
        title = [
            TextSpan("• ", bullet_style),
            TextSpan(_action_title(file), PATCH_TITLE_STYLE),
            TextSpan(" "),
            *_path_spans(file),
            TextSpan(" "),
            *_count_spans(file.added, file.removed, context=context),
        ]
    else:
        title = [
            TextSpan("• ", bullet_style),
            TextSpan("Edited", PATCH_TITLE_STYLE),
            TextSpan(f" {len(files)} files "),
            *_count_spans(added, removed, context=context),
        ]

    spans = _wrapped_row(
        title,
        terminal_width=terminal_width,
        continuation_prefix="  ",
        measure_width=measure_width,
    )
    for index, file in enumerate(files):
        if len(files) > 1:
            if index:
                spans.append(TextSpan("\n"))
            spans.append(TextSpan("\n"))
            spans.extend(_wrapped_row(
                [
                    TextSpan("  └ ", PATCH_MUTED_STYLE),
                    *_path_spans(file),
                    TextSpan(" "),
                    *_count_spans(file.added, file.removed, context=context),
                ],
                terminal_width=terminal_width,
                continuation_prefix="    ",
                measure_width=measure_width,
            ))
        spans.extend(_file_line_spans(
            file,
            context=context,
            terminal_width=terminal_width,
            measure_width=measure_width,
        ))
    return spans


def _file_line_spans(
    file: PatchFileView,
    *,
    context: DiffRenderStyleContext,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None
) -> list[TextSpan]:
    """生成单个文件中全部带行号的差异行。"""
    lines = [line for hunk in file.hunks for line in hunk.lines]
    if not lines:
        return []
    max_line = max(
        (line.new_line if line.kind != "remove" else line.old_line) or 0
        for line in lines
    )

    number_width = max(1, len(str(max_line)))
    syntax_path = file.new_path or file.old_path

    spans: list[TextSpan] = []

    for hunk_index, hunk in enumerate(file.hunks):
        syntax_lines = (
            highlight_code_lines(
                "\n".join(line.text for line in hunk.lines),
                path=syntax_path,
                light_theme=context.light,
            )
            if context.rich
            else None
        )
        if hunk_index:
            spans.extend((
                TextSpan("\n    "),
                TextSpan(f"{'':>{number_width}} ", PATCH_MUTED_STYLE),
                TextSpan("⋮", PATCH_MUTED_STYLE),
            ))
        for line_index, line in enumerate(hunk.lines):
            spans.append(TextSpan("\n"))
            spans.extend(_diff_line_spans(
                line,
                syntax_spans=(
                    list(syntax_lines[line_index])
                    if syntax_lines is not None
                    else None
                ),
                context=context,
                number_width=number_width,
                terminal_width=terminal_width,
                measure_width=measure_width,
            ))
    return spans


def _diff_line_spans(
    line: PatchLineView,
    *,
    syntax_spans: list[TextSpan] | None,
    context: DiffRenderStyleContext,
    number_width: int,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None
) -> list[TextSpan]:
    """生成一行差异及其不伪造行号的续行。"""
    number = line.old_line if line.kind == "remove" else line.new_line
    marker = "-" if line.kind == "remove" else "+" if line.kind == "add" else " "

    line_background = _line_background(line, context)
    gutter_style = _gutter_style(line, context, line_background=line_background)
    sign_style = _sign_style(
        line,
        context=context,
        line_background=line_background,
    )
    content_style = _content_style(
        line,
        context,
        line_background=line_background,
    )
    content = str(line.text or "").replace("\t", "    ")

    content_spans = (
        [_syntax_span(span, line=line, background=line_background) for span in syntax_spans]
        if syntax_spans is not None
        else [TextSpan(content, content_style)]
    )

    first_prefix = f"    {str(number or ''):>{number_width}} {marker}"
    continuation_prefix = f"    {'':>{number_width}}  "

    wrapped = wrap_styled_lines(
        content_spans,
        terminal_width=terminal_width or 1_000_000,
        first_prefix=first_prefix,
        continuation_prefix=continuation_prefix,
        measure_width=measure_width,
        hard=True,
    )

    spans: list[TextSpan] = []
    for index, row in enumerate(wrapped):
        if index:
            spans.append(TextSpan("\n"))
        spans.append(TextSpan("    ", _background_style(line_background)))
        if index == 0:
            spans.append(TextSpan(f"{str(number or ''):>{number_width}} ", gutter_style))
            spans.append(TextSpan(marker, sign_style))
        else:
            spans.append(TextSpan(f"{'':>{number_width}}  ", gutter_style))
        spans.extend(row)
    return spans


def _failure_spans(
    view: PatchView,
    *,
    context: DiffRenderStyleContext,
    terminal_width: int | None,
    measure_width: typing.Callable[[str], int] | None
) -> list[TextSpan]:
    """生成补丁失败标题及执行层诊断。"""
    failure_style = TextStyle(
        foreground=context.failure_foreground,
        bold=True,
    )
    spans = _wrapped_row(
        [
            TextSpan("✘ ", failure_style),
            TextSpan("Failed to apply patch", failure_style),
        ],
        terminal_width=terminal_width,
        continuation_prefix="  ",
        measure_width=measure_width,
    )
    for diagnostic in view.diagnostics:
        for value in diagnostic.values:
            spans.append(TextSpan("\n"))
            spans.extend(_wrapped_row(
                [
                    TextSpan("  "),
                    TextSpan(f"{diagnostic.label}: "),
                    TextSpan(value),
                ],
                terminal_width=terminal_width,
                continuation_prefix="    ",
                measure_width=measure_width,
            ))
    return spans


def _wrapped_row(
    parts: list[TextSpan],
    *,
    terminal_width: int | None,
    continuation_prefix: str,
    measure_width: typing.Callable[[str], int] | None
) -> list[TextSpan]:
    """按显示宽度硬换行一个已经包含首行前缀的样式行。"""
    if terminal_width is None:
        return parts
    return wrap_styled_line(
        parts,
        terminal_width=max(1, terminal_width),
        continuation_prefix=TextSpan(continuation_prefix, PATCH_MUTED_STYLE),
        measure_width=measure_width,
        hard=True,
    )


def create_diff_render_style_context(
    capabilities: TerminalCapabilities,
    *,
    scope_backgrounds: tuple[tuple[str, RgbColor], ...] = (),
) -> DiffRenderStyleContext:
    """为一次 patch 渲染解析终端能力和可选 syntax scope。"""

    light = _is_light_color(capabilities.theme.background)
    level = capabilities.color_support.effective_level
    colors_enabled = level in {
        TerminalColorLevel.TRUECOLOR,
        TerminalColorLevel.ANSI256,
        TerminalColorLevel.ANSI16,
    }
    add_foreground = "ansigreen" if colors_enabled else None
    remove_foreground = "ansired" if colors_enabled else None
    failure_foreground = "ansimagenta" if colors_enabled else None

    add_scope = _scope_background_color(
        scope_backgrounds,
        "markup.inserted",
        "diff.inserted",
        "diff.added",
    )

    remove_scope = _scope_background_color(
        scope_backgrounds,
        "markup.deleted",
        "diff.deleted",
        "diff.removed",
    )

    if level is TerminalColorLevel.TRUECOLOR:
        return DiffRenderStyleContext(
            light=light,
            rich=True,
            add_foreground=add_foreground,
            remove_foreground=remove_foreground,
            failure_foreground=failure_foreground,
            add_background=_palette_rgb(
                add_scope or ((218, 251, 225) if light else (33, 58, 43)),
                level,
            ),
            remove_background=_palette_rgb(
                remove_scope or ((255, 235, 233) if light else (74, 34, 29)),
                level,
            ),
            add_gutter_background=(
                _palette_rgb((172, 238, 187), level) if light else None
            ),
            remove_gutter_background=(
                _palette_rgb((255, 206, 203), level) if light else None
            ),
            gutter_foreground=(
                _palette_rgb((31, 35, 40), level) if light else None
            ),
        )
    if level is TerminalColorLevel.ANSI256:
        return DiffRenderStyleContext(
            light=light,
            rich=True,
            add_foreground=add_foreground,
            remove_foreground=remove_foreground,
            failure_foreground=failure_foreground,
            add_background=(
                _palette_rgb(add_scope, level)
                if add_scope is not None
                else _LIGHT_ANSI256_ADD_BG if light else _DARK_ANSI256_ADD_BG
            ),
            remove_background=(
                _palette_rgb(remove_scope, level)
                if remove_scope is not None
                else _LIGHT_ANSI256_REMOVE_BG if light else _DARK_ANSI256_REMOVE_BG
            ),
            add_gutter_background=(
                _LIGHT_ANSI256_ADD_GUTTER_BG if light else None
            ),
            remove_gutter_background=(
                _LIGHT_ANSI256_REMOVE_GUTTER_BG if light else None
            ),
            gutter_foreground=_LIGHT_ANSI256_GUTTER_FG if light else None,
        )
    return DiffRenderStyleContext(
        light=light,
        rich=False,
        add_foreground=add_foreground,
        remove_foreground=remove_foreground,
        failure_foreground=failure_foreground,
        add_background=None,
        remove_background=None,
        add_gutter_background=None,
        remove_gutter_background=None,
        gutter_foreground=None,
    )


def _palette_rgb(color: RgbColor, level: TerminalColorLevel) -> str:
    """选择目标颜色在当前色阶下的 prompt_toolkit 表示。"""
    return best_color(color, level) or "ansidefault"


def _scope_background_color(
    scope_backgrounds: tuple[tuple[str, RgbColor], ...],
    *scope_names: str
) -> RgbColor | None:
    """按作用域优先级读取补丁背景颜色。"""
    values = dict(scope_backgrounds)
    for name in scope_names:
        value = values.get(name)
        if (
            isinstance(value, tuple)
            and len(value) == 3
            and all(
                isinstance(component, int) and 0 <= component <= 255
                for component in value
            )
        ):
            return value
    return None


def _line_background(
    line: PatchLineView,
    context: DiffRenderStyleContext,
) -> str | None:
    """返回新增或删除视觉行的整行背景。"""
    if line.kind == "add":
        return context.add_background
    if line.kind == "remove":
        return context.remove_background
    return None


def _gutter_style(
    line: PatchLineView,
    context: DiffRenderStyleContext,
    *,
    line_background: str | None
) -> TextStyle:
    """返回行号区域在当前主题下的样式。"""
    if line.kind == "context" or not context.light:
        return TextStyle(background=line_background, dim=True)
    gutter_background = (
        context.add_gutter_background
        if line.kind == "add"
        else context.remove_gutter_background
    )
    return TextStyle(
        foreground=context.gutter_foreground,
        background=gutter_background,
    )


def _sign_style(
    line: PatchLineView,
    *,
    context: DiffRenderStyleContext,
    line_background: str | None
) -> TextStyle:
    """返回 diff 正负号在当前主题下的样式。"""
    if line.kind == "context":
        return TextStyle()
    foreground = (
        context.add_foreground
        if line.kind == "add"
        else context.remove_foreground
    )
    return TextStyle(foreground=foreground, background=line_background)


def _content_style(
    line: PatchLineView,
    context: DiffRenderStyleContext,
    *,
    line_background: str | None
) -> TextStyle:
    """返回未启用语法高亮时的 diff 正文样式。"""
    if line.kind == "context":
        return TextStyle()
    foreground = None
    if not context.light or not context.rich:
        foreground = (
            context.add_foreground
            if line.kind == "add"
            else context.remove_foreground
        )
    return TextStyle(foreground=foreground, background=line_background)


def _syntax_span(
    span: TextSpan,
    *,
    line: PatchLineView,
    background: str | None
) -> TextSpan:
    """把语法样式与 diff 行背景和删除弱化语义组合。"""
    style = replace(
        span.style,
        background=background,
        dim=span.style.dim or line.kind == "remove",
    )
    return TextSpan(span.text.replace("\t", "    "), style, span.hyperlink)


def _background_style(background: str | None) -> TextStyle:
    """返回只承载整行背景的样式。"""
    return TextStyle(background=background)


def _line_fill_styles(spans: list[TextSpan]) -> tuple[TextStyle | None, ...]:
    """从视觉行首背景生成不改变纯文本的右侧填充样式。"""
    backgrounds: list[str | None] = [None]
    for span in spans:
        chunks = span.text.split("\n")
        for index, chunk in enumerate(chunks):
            if chunk and backgrounds[-1] is None and span.style.background:
                backgrounds[-1] = span.style.background
            if index < len(chunks) - 1:
                backgrounds.append(None)
    return tuple(
        TextStyle(background=background) if background else None
        for background in backgrounds
    )


def _is_light_color(color: RgbColor | None) -> bool:
    """根据相对亮度识别浅色终端。"""
    if color is None:
        return False
    return is_light_color(color)


def _count_spans(
    added: int,
    removed: int,
    *,
    context: DiffRenderStyleContext,
) -> tuple[TextSpan, ...]:
    """生成分别表达增删语义的统计片段。"""
    return (
        TextSpan("("),
        TextSpan(
            f"+{added}",
            TextStyle(foreground=context.add_foreground),
        ),
        TextSpan(" "),
        TextSpan(
            f"-{removed}",
            TextStyle(foreground=context.remove_foreground),
        ),
        TextSpan(")"),
    )


def _path_spans(file: PatchFileView) -> tuple[TextSpan, ...]:
    """生成普通路径或重命名路径片段。"""
    if file.action == "rename":
        return (
            TextSpan(file.old_path or file.new_path),
            TextSpan(" → "),
            TextSpan(file.new_path or file.old_path),
        )
    return (TextSpan(file.old_path if file.action == "delete" else file.new_path),)


def _file_sort_path(file: PatchFileView) -> str:
    """返回用于稳定排序的规范化路径。"""
    return (file.old_path or file.new_path).replace("\\", "/")


def _action_title(file: PatchFileView) -> str:
    """返回单文件标题使用的动作名称。"""
    if file.action == "add":
        return "Added"
    if file.action == "delete":
        return "Deleted"
    return "Edited"


if __name__ == '__main__':
    pass
