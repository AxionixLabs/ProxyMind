# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from .models import (
    FormattedText,
    FragmentBlock
)

TuiBlockKind = typing.Literal[
    "user",
    "assistant",
    "operation",
    "plan",
    "approval",
    "notice",
    "system"
]

_COMPACT_TRANSITIONS: typing.Final[set[tuple[TuiBlockKind, TuiBlockKind]]] = {
    ("operation", "operation"),
    ("approval", "approval"),
    ("notice", "notice"),
    ("system", "system"),
}


@dataclass(frozen=True, slots=True)
class TranscriptBlock(object):
    """保存一项稳定正文、语义类型及其前置视觉间距。"""

    block: FragmentBlock
    kind: TuiBlockKind
    gap_before: bool = False


class TuiDocument(object):
    """管理稳定正文、动态正文和全局段间距。"""

    def __init__(self) -> None:
        self.blocks: list[TranscriptBlock]      = []
        self.committed_prefix_count: int        = 0
        self.active_block: FragmentBlock | None = None
        self.active_kind: TuiBlockKind | None   = None
        self.active_gap_before: bool            = False
        self.pending_gap: bool                  = False

    @property
    def has_content(self) -> bool:
        """返回当前是否存在稳定或动态正文。"""
        return bool(self.blocks or self.active_block is not None)

    @property
    def has_visible_content(self) -> bool:
        """返回实时画布中是否仍有未提交正文。"""
        return bool(
            len(self.blocks) > self.committed_prefix_count
            or self.active_block is not None
        )

    @property
    def visible_blocks(self) -> list[TranscriptBlock]:
        """返回尚未提交到终端滚屏区的稳定正文块。"""
        return self.blocks[self.committed_prefix_count:]

    def append_block(self, block: FragmentBlock, *, kind: TuiBlockKind) -> bool:
        """追加一个稳定正文块并按语义边界计算间距。"""
        self.blocks.append(TranscriptBlock(
            block=block,
            kind=kind,
            gap_before=self._consume_gap(kind),
        ))
        return True

    def discard_trailing_block(self, block: FragmentBlock) -> bool:
        """移除与指定对象相同的末尾稳定正文块。"""
        if (
            self.active_block is not None
            or len(self.blocks) <= self.committed_prefix_count
            or self.blocks[-1].block is not block
        ):
            return False
        self.blocks.pop()
        return True

    def request_gap(self) -> None:
        """请求在下一项真实正文前保留一个视觉空行。"""
        if self.has_content:
            self.pending_gap = True

    def set_active(self, block: FragmentBlock, *, kind: TuiBlockKind) -> None:
        """设置当前动态正文并在首次显示时计算语义间距。"""
        if self.active_block is None:
            self.active_kind = kind
            self.active_gap_before = self._consume_gap(kind)
        elif self.active_kind != kind:
            raise ValueError("active TUI block kind cannot change before commit")
        self.active_block = block

    def commit_active(self, block: FragmentBlock) -> None:
        """把当前动态正文替换为相同位置的稳定块。"""
        if self.active_kind is None:
            raise ValueError("cannot commit an active TUI block without a kind")
        self.blocks.append(TranscriptBlock(
            block=block,
            kind=self.active_kind,
            gap_before=self.active_gap_before,
        ))
        self.clear_active()

    def clear_active(self) -> None:
        """清空当前动态正文及其间距状态。"""
        self.active_block = None
        self.active_kind = None
        self.active_gap_before = False

    def fragments(self, *, width: int) -> FormattedText:
        """生成统一处理块边界后的正文片段。"""
        _ = width
        blocks = self.visible_blocks
        if self.active_block is not None:
            if self.active_kind is None:
                raise ValueError("active TUI block is missing its semantic kind")
            blocks.append(TranscriptBlock(
                block=self.active_block,
                kind=self.active_kind,
                gap_before=self.active_gap_before,
            ))
        return self._render_blocks(blocks)

    def stable_prefix_fragments(self, count: int) -> FormattedText:
        """生成下一批待提交稳定正文块的格式化片段。"""
        start = self.committed_prefix_count
        limit = max(0, min(len(self.blocks) - start, int(count)))
        return self._render_blocks(self.blocks[start:start + limit])

    def commit_stable_prefix(self, count: int) -> None:
        """标记下一批稳定正文已经写入终端滚屏区。"""
        remaining = len(self.blocks) - self.committed_prefix_count
        self.committed_prefix_count += max(0, min(remaining, int(count)))

    def all_fragments(self, *, width: int) -> FormattedText:
        """生成包含已提交前缀在内的完整对话片段。"""
        _ = width
        blocks = list(self.blocks)
        if self.active_block is not None:
            if self.active_kind is None:
                raise ValueError("active TUI block is missing its semantic kind")
            blocks.append(TranscriptBlock(
                block=self.active_block,
                kind=self.active_kind,
                gap_before=self.active_gap_before,
            ))
        return self._render_blocks(blocks)

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

    def _consume_gap(self, kind: TuiBlockKind) -> bool:
        """消费显式间距，并按相邻块语义决定默认间距。"""
        previous_kind = self.blocks[-1].kind if self.blocks else None

        gap_before = previous_kind is not None and (
            self.pending_gap
            or (previous_kind, kind) not in _COMPACT_TRANSITIONS
        )

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
