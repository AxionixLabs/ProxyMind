# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import os
import tempfile
import typing
from dataclasses import dataclass
from pathlib import Path

from agent.ports.presentation import ApplicationView
from agent.ports.presentation import TextSpan
from frontends.terminal.text import sanitize_terminal_text
from frontends.tui.adapters.clipboard import (
    ClipboardError,
    copy_text_to_clipboard,
)
from metadata import const
from ..contracts.menu import (
    STANDARD_MENU_FOOTER_HINT,
    MenuEmptyAcceptAction,
    MenuOption,
    MenuRequest,
)
from ..core.document import TranscriptBlock
from ..core.models import TranscriptExportResult
from ..core.styles import (
    BODY_STYLE,
    BRIGHT_STYLE,
    failure_text_block,
    fragment_block,
)
from ..rendering.fragments import fragments_text

if typing.TYPE_CHECKING:
    from ..application import TuiApplicationHost
    from ..core.runtime import TuiRuntime


@dataclass(frozen=True, slots=True)
class _TranscriptSection(object):
    """保存一段可独立导出的语义记录。"""
    kind: str
    text: str


class TranscriptExporter(object):
    """把完整会话以 Markdown 无覆盖地原子写入指定文件。"""

    def __init__(self, cwd: str | Path | None = None) -> None:
        self.cwd = Path(cwd or Path.cwd()).expanduser()

    def render(self, cells: typing.Iterable[TranscriptBlock]) -> str:
        """把冻结的会话记录转换为 Codex 同构的 Markdown。"""
        snapshot = tuple(cells)
        markdown = _markdown_text(snapshot)
        if markdown == f"# {const.APP_DESC} conversation\n":
            raise ValueError("No conversation content to export.")
        return markdown

    def export(
        self,
        cells: typing.Iterable[TranscriptBlock],
        requested_path: str | Path,
    ) -> TranscriptExportResult:
        """在工作目录内解析目标并以 no-clobber 语义创建文件。"""
        snapshot = tuple(cells)
        markdown = self.render(snapshot)
        target = _resolve_target(self.cwd, Path(requested_path))
        _atomic_create(target, markdown)
        return TranscriptExportResult(
            path=target,
            cell_count=len(snapshot),
        )


async def export_conversation(
    runtime: "TuiRuntime",
    host: "TuiApplicationHost",
    *,
    requested_path: str = "",
) -> None:
    """执行 Codex 同构的目的地选择、文件名输入和 Markdown 导出。"""
    cells = _runtime_cells(runtime)
    exporter = TranscriptExporter(host.history_workspace)
    path = requested_path.strip()

    if not path:
        destination = await runtime.select_menu(_destination_menu())
        if destination == "clipboard":
            await _copy_export(host, exporter, cells)
            return None
        if destination != "file":
            return None
        selected_path = await runtime.select_menu(
            _filename_prompt(host.conversation.sid)
        )
        if not isinstance(selected_path, str) or not selected_path.strip():
            return None
        path = selected_path.strip()

    await _write_export(host, exporter, cells, path)


def _runtime_cells(runtime: "TuiRuntime") -> tuple[TranscriptBlock, ...]:
    """冻结当前稳定记录及正在展示的动态尾部。"""
    snapshot = runtime.document.transcript_snapshot()
    live_cells = snapshot.live_tail.cells if snapshot.live_tail is not None else ()
    return (*snapshot.committed_cells, *live_cells)


def _destination_menu() -> MenuRequest:
    """返回与 Codex 文案和排列一致的导出目的地菜单。"""
    return MenuRequest(
        title="Export conversation",
        body=("Save the complete conversation as Markdown",),
        view_id="conversation:export-destination",
        footer_hint=STANDARD_MENU_FOOTER_HINT,
        options=(
            MenuOption(
                value="clipboard",
                label="Copy to clipboard",
                detail="Copy the complete Markdown transcript",
            ),
            MenuOption(
                value="file",
                label="Save to file",
                detail="Choose a Markdown filename",
            ),
        ),
    )


def _filename_prompt(session_id: str | None) -> MenuRequest:
    """返回带预填文件名的单行导出路径输入表面。"""
    normalized_id = str(session_id or "").strip()
    filename = (
        f"{const.APP_NAME}-session-{normalized_id}.md"
        if normalized_id
        else f"{const.APP_NAME}-session.md"
    )
    return MenuRequest(
        title="Save conversation",
        view_id="conversation:export-filename",
        text_input=True,
        initial_query=filename,
        text_input_gutter="▌",
        search_placeholder="",
        empty_accept_action=MenuEmptyAcceptAction.SUBMIT_QUERY,
        footer_hint=STANDARD_MENU_FOOTER_HINT,
        show_option_gutter=False,
        separate_options=False,
        surface_horizontal_inset=0,
    )


async def _copy_export(
    host: "TuiApplicationHost",
    exporter: TranscriptExporter,
    cells: tuple[TranscriptBlock, ...],
) -> None:
    """生成并复制 Markdown，随后写入稳定结果消息。"""
    try:
        markdown = exporter.render(cells)
        await copy_text_to_clipboard(markdown)
    except ClipboardError as error:
        _present_failure(host, f"Copy failed: {error}")
        return None
    except ValueError as error:
        _present_failure(host, str(error))
        return None
    _present_success(host, "Copied conversation to clipboard")


async def _write_export(
    host: "TuiApplicationHost",
    exporter: TranscriptExporter,
    cells: tuple[TranscriptBlock, ...],
    path: str,
) -> None:
    """在线程中创建 Markdown 文件并展示最终绝对路径。"""
    try:
        result = await asyncio.to_thread(exporter.export, cells, path)
    except (OSError, ValueError) as error:
        detail = sanitize_terminal_text(str(error)).strip()
        _present_failure(
            host,
            f"Export failed: {detail or type(error).__name__}",
        )
        return None
    _present_success(host, f"Saved conversation to {result.path}")


def _present_success(host: "TuiApplicationHost", message: str) -> None:
    """展示一条 Codex 同构的导出成功消息。"""
    host.frontend.application.emit(ApplicationView(
        type="tui.output",
        renderable=fragment_block(
            TextSpan("• ", BODY_STYLE),
            TextSpan(message, BRIGHT_STYLE),
        ),
    ))
    host.frontend.application.emit(ApplicationView(type="tui.gap"))


def _present_failure(host: "TuiApplicationHost", message: str) -> None:
    """展示一条导出失败消息。"""
    host.frontend.application.emit(ApplicationView(
        type="tui.output",
        renderable=failure_text_block(message),
    ))
    host.frontend.application.emit(ApplicationView(type="tui.gap"))


def _sections(
    cells: tuple[TranscriptBlock, ...],
) -> tuple[_TranscriptSection, ...]:
    """把连续记录 cell 合并为稳定的可导出语义段。"""
    out: list[_TranscriptSection] = []
    for cell in cells:
        text = _cell_text(cell)
        if (
            not text.strip("\n")
            or _is_session_info(cell, text.strip("\n"))
            or _is_export_feedback(text.strip("\n"))
        ):
            continue
        if cell.stream_continuation and out and out[-1].kind == cell.kind:
            previous = out[-1]
            separator = "" if previous.text.endswith("\n") else "\n"
            out[-1] = _TranscriptSection(
                kind=previous.kind,
                text=f"{previous.text}{separator}{text}",
            )
            continue
        out.append(_TranscriptSection(kind=cell.kind, text=text))
    return tuple(
        _TranscriptSection(
            kind=section.kind,
            text=section.text.strip("\n"),
        )
        for section in out
        if section.text.strip("\n")
    )


def _cell_text(cell: TranscriptBlock) -> str:
    """返回不包含终端样式和 OSC 元数据的记录文本。"""
    value = (
        cell.raw_text
        if cell.raw_text is not None
        else fragments_text(cell.transcript_block.fragments)
    )
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def _is_session_info(cell: TranscriptBlock, text: str) -> bool:
    """排除主界面启动标题，不把会话元信息导出为 Activity。"""
    return bool(
        cell.kind == "system"
        and text.startswith(f">_ {const.APP_DESC} (v")
    )


def _is_export_feedback(text: str) -> bool:
    """排除先前导出产生的成功或失败反馈。"""
    normalized = text.lstrip("•■ ")
    return normalized.startswith((
        "Saved conversation to ",
        "Copied conversation to clipboard",
        "Export failed: ",
        "Copy failed: ",
    ))


def _markdown_text(cells: tuple[TranscriptBlock, ...]) -> str:
    """生成与 Codex 分段语义一致的 Markdown transcript。"""
    markdown = f"# {const.APP_DESC} conversation\n"
    for section in _sections(cells):
        heading, indent = _section_presentation(section.kind)
        markdown += f"\n## {heading}\n\n"
        for line in section.text.split("\n"):
            markdown += f"{'    ' if indent else ''}{line}\n"
    return markdown


def _section_presentation(kind: str) -> tuple[str, bool]:
    """返回语义 cell 对应的导出标题和 Activity 缩进策略。"""
    if kind == "user":
        return "User", False
    if kind == "assistant":
        return "Assistant", False
    if kind == "plan":
        return "Plan", False
    return "Activity", True


def _resolve_target(cwd: Path, requested_path: Path) -> Path:
    """按 Codex 语义解析 home、绝对路径和工作区相对路径。"""
    expanded = requested_path.expanduser()
    return expanded if expanded.is_absolute() else cwd / expanded


def _atomic_create(path: Path, content: str) -> None:
    """在目标目录内落临时文件并原子创建，已存在文件绝不覆盖。"""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=const.CHARSET,
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as file:
            temporary_path = Path(file.name)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.link(temporary_path, path)
    except FileExistsError as error:
        raise FileExistsError(
            f"could not create {path}: file already exists"
        ) from error
    except OSError as error:
        raise OSError(f"could not create {path}: {error}") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


if __name__ == '__main__':
    pass
