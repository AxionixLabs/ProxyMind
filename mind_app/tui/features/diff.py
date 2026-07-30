# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.frontend import ApplicationView
from mind_app.presentation.models import (
    TextSpan,
    TextStyle
)
from ..core.models import FragmentBlock
from ..core.styles import (
    MUTED_STYLE,
    command_result_block,
    prompt_style
)

DIFF_DISPLAY_MAX_LINES = 300
DIFF_DISPLAY_MAX_CHARS = 40_000

if typing.TYPE_CHECKING:
    from ...controller import Mind


def print_current_apply_patch_diff(mind: "Mind") -> None:
    """展示当前 apply_patch 净差异。"""
    application = mind.frontend.application

    snapshot = mind.native_coding.patch_diff_snapshot()
    if bool(snapshot.get("invalidated")):
        application.emit(ApplicationView(
            type="tui.diff.unavailable",
            renderable=command_result_block(
                "/diff",
                TextSpan(
                    "Unavailable: current apply_patch delta is not exact.",
                    MUTED_STYLE,
                ),
            ),
        ))
        application.emit(ApplicationView(type="tui.gap"))
        return None

    diff_text = str(snapshot.get("diff") or "")
    if not diff_text.strip():
        application.emit(ApplicationView(
            type="tui.diff.empty",
            renderable=command_result_block(
                "/diff",
                TextSpan("No apply_patch changes in current turn.", MUTED_STYLE),
            ),
        ))
        application.emit(ApplicationView(type="tui.gap"))
        return None

    display_text, truncated = truncate_diff_text(diff_text)

    files, added, removed = diff_stat(diff_text)

    application.emit(ApplicationView(
        type="tui.diff.title",
        renderable=command_result_block(
            "/diff",
            TextSpan(
                f"{files} {'file' if files == 1 else 'files'} changed"
                f" · +{added} -{removed}",
                MUTED_STYLE,
            ),
        ),
    ))
    application.emit(ApplicationView(type="tui.gap"))
    application.emit(ApplicationView(
        type="tui.diff.body",
        renderable=render_diff_text(display_text),
    ))
    if truncated:
        application.emit(ApplicationView(
            type="tui.diff.truncated",
            renderable=_text_block(
                "... diff truncated",
                TextStyle(foreground="#7F8C9A", dim=True),
            ),
        ))
    application.emit(ApplicationView(type="tui.gap"))


def render_diff_text(diff_text: str) -> FragmentBlock:
    """按 diff 语义生成彩色文本。"""
    fragments: list[tuple[str, str]] = []
    for raw_line in str(diff_text or "").splitlines():
        fragments.append((prompt_style(diff_line_style(raw_line)), raw_line))
        fragments.append(("", "\n"))
    if fragments:
        fragments.pop()
    return FragmentBlock(tuple(fragments))


def diff_line_style(line: str) -> TextStyle:
    """返回单行 diff 的终端显示样式。"""
    if line.startswith("diff --git "):
        return TextStyle(foreground="#7DD3FC", bold=True)
    if line.startswith("@@"):
        return TextStyle(foreground="#FACC15", bold=True)
    if line.startswith("+++"):
        return TextStyle(foreground="#6EE7A8", bold=True)
    if line.startswith("---"):
        return TextStyle(foreground="#FF8A8A", bold=True)
    if line.startswith("+"):
        return TextStyle(foreground="#6EE7A8")
    if line.startswith("-"):
        return TextStyle(foreground="#FF8A8A")
    if line.startswith((
        "index ",
        "new file mode ",
        "deleted file mode ",
        "rename from ",
        "rename to ",
        "similarity index "
    )):
        return TextStyle(foreground="#7F8C9A", dim=True)

    if line.startswith("diff omitted:"):
        return TextStyle(foreground="#FFB86B", dim=True)

    return TextStyle(foreground="#CBD5E1")


def _text_block(text: str, style: TextStyle) -> FragmentBlock:
    """生成单样式 TUI 文本块。"""
    return FragmentBlock(((prompt_style(style), text),))


def truncate_diff_text(
    diff_text: str,
    *,
    max_lines: int = DIFF_DISPLAY_MAX_LINES,
    max_chars: int = DIFF_DISPLAY_MAX_CHARS
) -> tuple[str, bool]:
    """按固定上限截断 diff 展示文本。"""
    lines = str(diff_text or "").splitlines(keepends=True)

    clipped: list[str] = []
    used_chars: int    = 0
    truncated: bool    = False

    for line in lines:
        if len(clipped) >= max_lines:
            truncated = True
            break
        if used_chars + len(line) > max_chars:
            remaining = max(0, max_chars - used_chars)
            if remaining > 0:
                clipped.append(line[:remaining])
            truncated = True
            break

        clipped.append(line)
        used_chars += len(line)

    if not truncated and len(clipped) < len(lines):
        truncated = True

    return "".join(clipped).rstrip("\n"), truncated


def diff_stat(diff_text: str) -> tuple[int, int, int]:
    """统计 diff 文件数和增删行数。"""
    files: int   = 0
    added: int   = 0
    removed: int = 0

    for line in str(diff_text or "").splitlines():
        if line.startswith("diff --git "):
            files += 1
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
            continue
        if line.startswith("-") and not line.startswith("---"):
            removed += 1

    return files, added, removed


if __name__ == '__main__':
    pass
