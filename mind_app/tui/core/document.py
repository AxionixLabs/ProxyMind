# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass
from .models import (
    FormattedText,
    FragmentBlock
)


@dataclass(frozen=True, slots=True)
class TranscriptBlock(object):
    """保存一项稳定正文及其前置视觉间距。"""

    block: FragmentBlock
    gap_before: bool = False


class TuiDocument(object):
    """管理稳定正文、动态正文和全局段间距。"""

    def __init__(self) -> None:
        self.blocks: list[TranscriptBlock]      = []
        self.active_block: FragmentBlock | None = None
        self.active_gap_before: bool            = False
        self.pending_gap: bool                  = False

    @property
    def has_content(self) -> bool:
        """返回当前是否存在稳定或动态正文。"""
        return bool(self.blocks or self.active_block is not None)

    def append_block(self, block: FragmentBlock) -> bool:
        """追加一个稳定正文块并消费待处理间距。"""
        self.blocks.append(TranscriptBlock(
            block=block,
            gap_before=self._consume_pending_gap(),
        ))
        return True

    def discard_trailing_block(self, block: FragmentBlock) -> bool:
        """移除与指定对象相同的末尾稳定正文块。"""
        if (
            self.active_block is not None
            or not self.blocks
            or self.blocks[-1].block is not block
        ):
            return False
        self.blocks.pop()
        return True

    def request_gap(self) -> None:
        """请求在下一项真实正文前保留一个视觉空行。"""
        if self.has_content:
            self.pending_gap = True

    def set_active(self, block: FragmentBlock) -> None:
        """设置当前动态正文并在首次显示时消费间距。"""
        if self.active_block is None:
            self.active_gap_before = self._consume_pending_gap()
        self.active_block = block

    def commit_active(self, block: FragmentBlock) -> None:
        """把当前动态正文替换为相同位置的稳定块。"""
        self.blocks.append(TranscriptBlock(
            block=block,
            gap_before=self.active_gap_before,
        ))
        self.clear_active()

    def clear_active(self) -> None:
        """清空当前动态正文及其间距状态。"""
        self.active_block = None
        self.active_gap_before = False

    def fragments(self, *, width: int) -> FormattedText:
        """生成统一处理块边界后的正文片段。"""
        _ = width
        blocks = list(self.blocks)
        if self.active_block is not None:
            blocks.append(TranscriptBlock(
                block=self.active_block,
                gap_before=self.active_gap_before,
            ))
        return self._render_blocks(blocks)

    def stable_prefix_fragments(self, count: int) -> FormattedText:
        """生成指定数量稳定正文块的格式化片段。"""
        limit = max(0, min(len(self.blocks), int(count)))
        return self._render_blocks(self.blocks[:limit])

    def discard_stable_prefix(self, count: int) -> None:
        """移除已经提交到终端滚屏区的稳定正文前缀。"""
        limit = max(0, min(len(self.blocks), int(count)))
        if limit:
            del self.blocks[:limit]

    def _render_blocks(self, blocks: list[TranscriptBlock]) -> FormattedText:
        """统一渲染一组正文块及其前置间距。"""
        out: FormattedText = []
        for block in blocks:
            parts = self._trim_block_fragments(list(block.block.fragments))
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
