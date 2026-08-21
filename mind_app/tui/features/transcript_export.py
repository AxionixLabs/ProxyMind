# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import re
import typing
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from mind_app.paths import mind_reports_dir
from mind_nova import const
from ..core.document import TranscriptBlock
from ..core.models import TranscriptExportFormat
from ..rendering.fragments import fragments_text


@dataclass(frozen=True, slots=True)
class TranscriptExportResult(object):
    """描述一次记录导出的文件和内容规模。"""
    path: Path
    format: TranscriptExportFormat
    cell_count: int


@dataclass(frozen=True, slots=True)
class _TranscriptSection(object):
    """保存一段可独立导出的语义记录。"""
    kind: str
    text: str


class TranscriptExporter(object):
    """把语义记录以 Markdown 或原始文本原子写入文件。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or mind_reports_dir() / "transcripts").expanduser()

    def export(
        self,
        cells: typing.Iterable[TranscriptBlock],
        output_format: TranscriptExportFormat
    ) -> TranscriptExportResult:
        """生成指定格式的记录文件并返回最终路径。"""
        snapshot = tuple(cells)
        if not snapshot:
            raise ValueError("transcript is empty")
        if output_format not in {"markdown", "raw"}:
            raise ValueError(
                f"unsupported transcript export format: {output_format}"
            )

        content = (
            _markdown_text(snapshot)
            if output_format == "markdown"
            else _raw_text(snapshot)
        )
        suffix = ".md" if output_format == "markdown" else ".txt"
        self.root.mkdir(parents=True, exist_ok=True)
        target = _unique_target(self.root, suffix=suffix)
        _atomic_write(target, content)

        return TranscriptExportResult(
            path=target,
            format=output_format,
            cell_count=len(snapshot),
        )


def _sections(
    cells: tuple[TranscriptBlock, ...]
) -> tuple[_TranscriptSection, ...]:
    """把连续记录 cell 合并为稳定的语义段。"""
    out: list[_TranscriptSection] = []

    for cell in cells:
        text = _cell_text(cell)
        if not text:
            continue
        if (
            cell.stream_continuation
            and out
            and out[-1].kind == cell.kind
        ):
            previous = out[-1]
            out[-1] = _TranscriptSection(
                kind=previous.kind,
                text=_join_continuation(previous.text, text),
            )
            continue
        out.append(_TranscriptSection(kind=cell.kind, text=text))

    return tuple(
        _TranscriptSection(kind=section.kind, text=section.text.strip("\n"))
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
    return (
        str(value or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )


def _join_continuation(previous: str, current: str) -> str:
    """连接同一语义段的后续文本并保留已有换行边界。"""
    if previous.endswith("\n") or current.startswith("\n"):
        return previous + current
    return f"{previous}\n{current}"


def _raw_text(cells: tuple[TranscriptBlock, ...]) -> str:
    """生成无展示装饰的纯文本记录。"""
    sections = _sections(cells)
    return "\n\n".join(section.text for section in sections).rstrip() + "\n"


def _markdown_text(cells: tuple[TranscriptBlock, ...]) -> str:
    """生成保留消息 Markdown 并隔离工具输出的记录。"""
    parts = ["# Transcript"]

    for section in _sections(cells):
        heading = _section_heading(section.kind)
        body = (
            _fenced_text(section.text)
            if section.kind == "operation"
            else section.text
        )
        parts.append(f"## {heading}\n\n{body}")

    return "\n\n".join(parts).rstrip() + "\n"


def _section_heading(kind: str) -> str:
    """返回记录类型对应的稳定 Markdown 标题。"""
    return {
        "user": "User",
        "assistant": "Assistant",
        "operation": "Tool",
        "plan": "Plan",
        "approval": "Approval",
        "notice": "Notice",
        "system": "System",
    }.get(str(kind or ""), "Event")


def _fenced_text(text: str) -> str:
    """使用不会与正文冲突的 Markdown 围栏包裹文本。"""
    longest = max(
        (len(match.group(0)) for match in re.finditer(r"`+", text)),
        default=0,
    )
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"


def _unique_target(root: Path, *, suffix: str) -> Path:
    """返回当前目录下尚未占用的时间戳文件名。"""
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    base = root / f"transcript-{timestamp}{suffix}"
    if not base.exists():
        return base

    index = 2
    while True:
        candidate = root / f"transcript-{timestamp}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _atomic_write(path: Path, content: str) -> None:
    """先写入同目录临时文件，再原子替换最终文件。"""
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
        os.replace(temporary_path, path)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


if __name__ == '__main__':
    pass
