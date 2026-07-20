# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from rich.text import Text
from mind_app.frontend import ApplicationView

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
            renderable=Text(
                "Diff unavailable: current apply_patch delta is not exact.",
                style="dim #7F8C9A"
            ),
        ))
        application.emit(ApplicationView(type="tui.gap"))
        return None

    diff_text = str(snapshot.get("diff") or "")
    if not diff_text.strip():
        application.emit(ApplicationView(
            type="tui.diff.empty",
            renderable=Text(
                "No apply_patch diff in current turn.",
                style="dim #7F8C9A",
            ),
        ))
        application.emit(ApplicationView(type="tui.gap"))
        return None

    display_text, truncated = truncate_diff_text(diff_text)

    files, added, removed = diff_stat(diff_text)

    application.emit(ApplicationView(
        type="tui.diff.title",
        renderable=Text(
            "Diff · current apply_patch changes",
            style="bold #AFC7D8",
        ),
    ))
    application.emit(ApplicationView(
        type="tui.diff.stat",
        renderable=Text(
            f"{files} {'file' if files == 1 else 'files'} changed · +{added} -{removed}",
            style="#7F8C9A"
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
            renderable=Text("... diff truncated", style="dim #7F8C9A"),
        ))
    application.emit(ApplicationView(type="tui.gap"))


def render_diff_text(diff_text: str) -> Text:
    """按 diff 语义生成彩色文本。"""
    out = Text()
    for raw_line in str(diff_text or "").splitlines():
        out.append(raw_line, style=diff_line_style(raw_line))
        out.append("\n")
    if out:
        out.rstrip()
    return out


def diff_line_style(line: str) -> str:
    """返回单行 diff 的终端显示样式。"""
    if line.startswith("diff --git "):
        return "bold #7DD3FC"
    if line.startswith("@@"):
        return "bold #FACC15"
    if line.startswith("+++"):
        return "bold #6EE7A8"
    if line.startswith("---"):
        return "bold #FF8A8A"
    if line.startswith("+"):
        return "#6EE7A8"
    if line.startswith("-"):
        return "#FF8A8A"
    if line.startswith((
        "index ",
        "new file mode ",
        "deleted file mode ",
        "rename from ",
        "rename to ",
        "similarity index "
    )):
        return "dim #7F8C9A"

    if line.startswith("diff omitted:"):
        return "dim #FFB86B"

    return "#CBD5E1"


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
