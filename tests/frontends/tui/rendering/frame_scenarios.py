# -*- coding: utf-8 -*-

"""提供 TUI frame 场景的输入构造、同步等待和可见事实读取。"""

import asyncio
from pathlib import Path

from prompt_toolkit.data_structures import Size
from prompt_toolkit.layout.screen import Screen
from prompt_toolkit.output import DummyOutput

from frontends.tui.core.document import TuiDocument
from frontends.tui.core.models import FragmentBlock
from frontends.tui.core.runtime import TuiRuntime
from infrastructure.skills import SkillSpec


class AlternateScreenOutput(DummyOutput):
    """记录备用屏幕进入与退出并提供可控终端尺寸。"""

    def __init__(self, *, columns: int = 80, rows: int = 24) -> None:
        self.size = Size(rows=rows, columns=columns)
        self.enter_count = 0
        self.quit_count = 0

    def get_size(self) -> Size:
        """返回当前可控终端尺寸。"""
        return self.size

    def enter_alternate_screen(self) -> None:
        """记录备用屏幕进入。"""
        self.enter_count += 1

    def quit_alternate_screen(self) -> None:
        """记录备用屏幕退出。"""
        self.quit_count += 1


class KnownInlineHeightOutput(AlternateScreenOutput):
    """提供可控光标下方行数的内联终端输出。"""

    def __init__(
        self,
        *,
        columns: int = 80,
        rows: int = 24,
        available_rows: int,
    ) -> None:
        super().__init__(columns=columns, rows=rows)
        self.available_rows = available_rows

    def get_rows_below_cursor_position(self) -> int:
        """返回测试设置的可用内联行数。"""
        return self.available_rows


def block(text: str) -> FragmentBlock:
    """创建仅含纯文本的 TUI 内容块。"""
    return FragmentBlock((("", text),))


def document_text(document: TuiDocument) -> str:
    """读取文档当前可见片段的纯文本事实。"""
    return "".join(text for _style, text in document.fragments(width=80))


def transcript_text(document: TuiDocument) -> str:
    """读取文档完整 transcript 的纯文本事实。"""
    return "".join(
        text for _style, text in document.transcript_fragments(width=80)
    )


async def render_next_frame(runtime: TuiRuntime) -> Screen:
    """触发渲染并返回完成后的屏幕。"""
    previous_revision = runtime.screen.application.render_counter
    runtime.invalidate()
    for _ in range(20):
        await asyncio.sleep(0)
        if runtime.screen.application.render_counter > previous_revision:
            return runtime.screen.application.renderer.last_rendered_screen
    raise AssertionError("next frame was not rendered")


def rendered_screen_text(screen: Screen) -> str:
    """返回渲染屏幕中的逐行文本。"""
    return "\n".join(
        "".join(
            cells[column].char
            for column in sorted(cells)
        ).rstrip()
        for _row, cells in sorted(screen.data_buffer.items())
    )


def first_nonblank_screen_row(screen: Screen) -> int:
    """返回首个包含可见文本的屏幕行。"""
    for row, cells in sorted(screen.data_buffer.items()):
        text = "".join(
            cells[column].char
            for column in sorted(cells)
        )
        if text.strip():
            return row
    raise AssertionError("screen does not contain visible text")


async def wait_for_input_text(runtime: TuiRuntime, text: str) -> None:
    """等待管道输入被主输入框完整消费。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0

    while loop.time() < deadline:
        if runtime.screen.input.buffer.text == text:
            return
        await asyncio.sleep(0.001)

    actual = runtime.screen.input.buffer.text
    if actual == text:
        return
    raise AssertionError(f"input text did not become {text!r}: {actual!r}")


async def wait_for_scrollback_advance(
    runtime: TuiRuntime,
    *,
    after: int = 0,
) -> int:
    """等待原生滚屏游标推进且当前提交任务结束。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0

    while loop.time() < deadline:
        cursor = runtime.document.scrollback_line_count
        if cursor > after and runtime.viewport.scrollback_task is None:
            return cursor
        await asyncio.sleep(0.001)

    raise AssertionError("scrollback did not advance")


async def wait_for_scrollback_settlement(runtime: TuiRuntime) -> None:
    """等待待处理的原生滚屏事务完成或确认无需提交。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0

    while loop.time() < deadline:
        if (
            runtime.viewport.scrollback_task is None
            and runtime.viewport._scrollback_render_revision is None
        ):
            return
        await asyncio.sleep(0.001)

    raise AssertionError("scrollback did not settle")


def spacing_test_skill(name: str) -> SkillSpec:
    """创建布局测试使用的 skill 描述。"""
    entry = Path(f"{name}/SKILL.md")
    return SkillSpec(
        name=name,
        description=f"Use {name}",
        source="test",
        root=entry.parent,
        entry=entry,
    )
