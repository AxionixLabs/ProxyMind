# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import random
import typing
import asyncio
from mind_app.output.contracts import OutputControlPort
from mind_app.presentation.models import (
    StyledBlock,
    TextStyle
)
from mind_app.stream_io.output_record import StreamRecordWriter
from mind_app.stream_sanitize import sanitize_value
from ..core.assistant import TuiAssistantStream
from ..core.document import TuiBlockKind
from ..core.runtime import TuiRuntime
from ..core.models import FragmentBlock
from ..core.render import (
    display_line_count,
    fragments_text
)
from ..core.styles import (
    ASSISTANT_PREFIX_CLASS,
    prompt_style,
    styled_block_fragments
)
from .markdown import render_tui_markdown

TYPEWRITER_CURSOR_STYLE = TextStyle(foreground="#D7E7FF", bold=True)


class TuiOutputControl(OutputControlPort):
    """把单轮流式内容写入持久 TUI。"""

    def __init__(
        self,
        log_file: str,
        *,
        runtime: TuiRuntime,
        animate: bool = True,
    ) -> None:
        self.log_file = log_file
        self.runtime  = runtime
        self.animate  = bool(animate)

        self.assistant     = TuiAssistantStream()
        self.record_writer = StreamRecordWriter(log_file)
        self._cursor = random.choice(("█", "▉", "▋"))

    @property
    def terminal_width(self) -> int | None:
        """返回当前 TUI 宽度。"""
        return self.runtime.terminal_width

    @property
    def terminal_height(self) -> int | None:
        """返回当前 TUI 高度。"""
        return self.runtime.terminal_height

    async def open(self) -> None:
        """打开当前输出记录。"""
        await self.record_writer.open()

    async def stop(self, *, blink: bool = True) -> None:
        """提交当前内容并关闭记录。"""
        _ = blink
        self._commit_current()
        await self.record_writer.close()

    async def append_assistant_delta(
        self,
        chunk: typing.Optional[str],
    ) -> None:
        """向当前 TUI assistant 正文追加一段原始增量。"""
        if not chunk:
            return None

        text = self.assistant.prepare_delta(str(chunk))
        self.record_writer.write(text)

        if self.animate:
            await self._append_typewriter(text)
            return None

        self.assistant.append(text)
        self._render_active(cursor=False)

    async def prepare_external_output(self) -> None:
        """在外部展示前提交当前流式内容。"""
        self.assistant.discard_boundary()
        self._commit_current()

    async def settle_stream(self) -> None:
        """立即同步当前流式内容。"""
        if self.assistant.active:
            self._render_active(cursor=False)

    async def record_hidden_output(self, text: str) -> None:
        """记录不直接展示的块状内容。"""
        if text:
            value = str(text)
            self.assistant.discard_boundary()
            self.record_writer.write(value, block=True)

    def mark_stream_boundary(self) -> None:
        """标记下一段流式内容边界。"""
        self.assistant.mark_boundary()

    def record_tool_arguments(
        self,
        name: str,
        arguments: dict[str, typing.Any],
        *,
        call_id: typing.Optional[str] = None,
    ) -> None:
        """记录工具调用参数审计信息。"""
        call_part = f" call_id={call_id}" if call_id else ""
        self.record_writer.write_audit(
            f"# tool_args tool={name}{call_part} arguments={self._audit_payload(arguments)}"
        )

    async def append_assistant_metadata(
        self,
        text: typing.Optional[str],
    ) -> None:
        """提交正文后追加一项 assistant 元数据块。"""
        if not text:
            return None
        self.assistant.discard_boundary()
        self._commit_current()

        value = str(text)
        self.record_writer.write(value, block=True)
        block = StyledBlock(plain_text=value.rstrip("\n"))
        self.runtime.append_block(
            FragmentBlock(styled_block_fragments(
                block,
            )),
            kind="assistant",
        )

    async def append_presentation_block(
        self,
        block: StyledBlock,
        *,
        block_kind: TuiBlockKind = "operation",
    ) -> None:
        """提交正文后追加一个结构化展示块。"""
        if not block.plain_text:
            return None
        self.assistant.discard_boundary()
        self._commit_current()
        self.record_writer.write(block.plain_text, block=True)
        self.runtime.append_block(
            FragmentBlock(styled_block_fragments(
                block,
            )),
            kind=block_kind,
        )

    def flush(self) -> None:
        """刷新当前输出记录。"""
        self.record_writer.flush()

    def _commit_current(self) -> bool:
        """把当前动态内容提交为稳定 TUI 内容块并返回提交状态。"""
        if not self.assistant.active:
            self.runtime.clear_active_renderable()
            return False
        block = _assistant_prefixed_block(render_tui_markdown(self.assistant.text))
        self.runtime.commit_active_renderable(block)
        self.assistant.clear()
        return True

    async def _append_typewriter(self, text: str) -> None:
        """按现有打字机节奏分批展示流式文本。"""
        size = max(1, len(text))
        for index in range(0, len(text), 2):
            delta = text[index:index + 2]
            self.assistant.append(delta)
            self._render_active(cursor=True)

            progress = index / max(1, size - 1)
            delay    = 0.010 + (0.0065 - 0.010) * progress

            if any(char in "。.!！?？" for char in delta):
                delay += 0.035
            elif any(char in "；;：:" for char in delta):
                delay += 0.025
            elif any(char in "，," for char in delta):
                delay += 0.015
            await asyncio.sleep(max(0.0015, delay))

    def _render_active(
        self,
        *,
        cursor: bool,
    ) -> None:
        """刷新当前流式内容并按需附加打字机光标。"""
        block = StyledBlock(plain_text=self.assistant.text)
        fragments = _assistant_prefixed_fragments(
            list(styled_block_fragments(block))
        )
        if cursor and _cursor_keeps_display_height(
            fragments,
            self._cursor,
            width=self.terminal_width,
        ):
            fragments.append((prompt_style(TYPEWRITER_CURSOR_STYLE), self._cursor))

        self.runtime.set_active_renderable(
            FragmentBlock(tuple(fragments)),
            kind="assistant",
        )

    @staticmethod
    def _audit_payload(arguments: dict[str, typing.Any]) -> str:
        """生成工具参数审计 JSON。"""
        try:
            value = sanitize_value(arguments, max_depth=6, max_items=50)
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        except Exception as exc:
            return json.dumps(
                {"error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                separators=(",", ":"),
            )


def _assistant_prefixed_block(block: FragmentBlock) -> FragmentBlock:
    """给助手正文块添加单个项目符号前缀。"""
    return FragmentBlock(tuple(_assistant_prefixed_fragments(list(block.fragments))))


def _cursor_keeps_display_height(
    fragments: list[tuple[str, str]],
    cursor: str,
    *,
    width: int | None,
) -> bool:
    """判断打字机光标是否不会单独增加正文显示行。"""
    if width is None:
        return True

    text       = fragments_text(fragments)
    line_width = max(1, int(width))

    return display_line_count(
        f"{text}{cursor}",
        width=line_width,
    ) == display_line_count(text, width=line_width)


def _assistant_prefixed_fragments(
    fragments: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """移除正文前导换行并添加助手项目符号和续行缩进。"""
    out = [(style, text) for style, text in fragments if text]
    while out:
        style, text = out[0]
        trimmed = text.lstrip("\r\n")
        if trimmed:
            out[0] = style, trimmed
            break
        out.pop(0)
    if not out:
        return []
    prefix_style = ASSISTANT_PREFIX_CLASS
    return [
        (prefix_style, "• "),
        *_assistant_continuation_fragments(out, indent_style=prefix_style),
    ]


def _assistant_continuation_fragments(
    fragments: list[tuple[str, str]],
    *,
    indent_style: str,
) -> list[tuple[str, str]]:
    """在助手正文每个显式续行前补充两个空格。"""
    out: list[tuple[str, str]] = []
    continuation = False

    for style, text in fragments:
        lines = text.split("\n")
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

    return out


if __name__ == '__main__':
    pass
