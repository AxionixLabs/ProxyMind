# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from .render import (
    FormattedText,
    renderable_fragments
)


@dataclass(frozen=True, slots=True)
class TranscriptBlock(object):
    """保存一项稳定正文及其前置视觉间距。"""

    renderable: typing.Any
    gap_before: bool = False


class TuiDocument(object):
    """管理稳定正文、动态正文和全局段间距。"""

    def __init__(self) -> None:
        self.blocks: list[TranscriptBlock] = []
        self.active_renderable: typing.Any = None
        self.active_gap_before = False
        self.pending_gap = False

    @property
    def has_content(self) -> bool:
        """返回当前是否存在稳定或动态正文。"""
        return bool(self.blocks or self.active_renderable is not None)

    def append_block(self, renderable: typing.Any) -> bool:
        """追加一个稳定正文块并消费待处理间距。"""
        if renderable is None:
            return False
        self.blocks.append(TranscriptBlock(
            renderable=renderable,
            gap_before=self._consume_pending_gap(),
        ))
        return True

    def request_gap(self) -> None:
        """请求在下一项真实正文前保留一个视觉空行。"""
        if self.has_content:
            self.pending_gap = True

    def set_active(self, renderable: typing.Any) -> None:
        """设置当前动态正文并在首次显示时消费间距。"""
        if self.active_renderable is None and renderable is not None:
            self.active_gap_before = self._consume_pending_gap()
        self.active_renderable = renderable

    def commit_active(self, renderable: typing.Any) -> None:
        """把当前动态正文替换为相同位置的稳定块。"""
        if renderable is not None:
            self.blocks.append(TranscriptBlock(
                renderable=renderable,
                gap_before=self.active_gap_before,
            ))
        self.clear_active()

    def clear_active(self) -> None:
        """清空当前动态正文及其间距状态。"""
        self.active_renderable = None
        self.active_gap_before = False

    def fragments(self, *, width: int) -> FormattedText:
        """生成统一处理块边界后的正文片段。"""
        out: FormattedText = []
        blocks = list(self.blocks)
        if self.active_renderable is not None:
            blocks.append(TranscriptBlock(
                renderable=self.active_renderable,
                gap_before=self.active_gap_before,
            ))

        for block in blocks:
            parts = self._trim_block_fragments(
                renderable_fragments(block.renderable, width=width)
            )
            if not parts:
                continue
            if out:
                out.append(("", "\n\n" if block.gap_before else "\n"))
            out.extend(parts)
        return out

    def _consume_pending_gap(self) -> bool:
        """消费待处理间距并避免首项正文产生前导空行。"""
        gap_before = bool(self.blocks and self.pending_gap)
        self.pending_gap = False
        return gap_before

    @staticmethod
    def _trim_block_fragments(parts: FormattedText) -> FormattedText:
        """移除正文块外侧换行并保留块内原始结构。"""
        out = [(style, text) for style, text in parts if text]
        while out:
            style, text = out[0]
            trimmed = text.lstrip("\r\n")
            if trimmed:
                out[0] = style, trimmed
                break
            out.pop(0)
        while out:
            style, text = out[-1]
            trimmed = text.rstrip("\r\n")
            if trimmed:
                out[-1] = style, trimmed
                break
            out.pop()
        return out


if __name__ == '__main__':
    pass
