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

@dataclass(frozen=True, slots=True)
class TranscriptBlock(object):
    """保存一项稳定正文、语义类型及其前置视觉间距。"""

    block: FragmentBlock
    kind: TuiBlockKind
    gap_before: bool = False


class TuiDocument(object):
    """管理稳定正文、动态正文和全局段间距。"""

    def __init__(self) -> None:
        self.blocks: list[TranscriptBlock]       = []
        self.scrollback_prefix_count: int        = 0
        self.cleared_prefix_count: int           = 0
        self.active_block: FragmentBlock | None  = None
        self.active_kind: TuiBlockKind | None    = None
        self.active_gap_before: bool             = False

        self._active_tail: list[TranscriptBlock]       = []
        self._pending_submission: FragmentBlock | None = None

    @property
    def has_pending_submission(self) -> bool:
        """返回是否存在尚未决定展示方式的用户输入。"""
        return self._pending_submission is not None

    @property
    def visible_prefix_count(self) -> int:
        """返回实时正文应跳过的稳定块数量。"""
        return max(
            self.scrollback_prefix_count,
            self.cleared_prefix_count,
        )

    @property
    def has_content(self) -> bool:
        """返回当前是否存在稳定或动态正文。"""
        return bool(
            self.blocks
            or self.active_block is not None
            or self._active_tail
        )

    @property
    def has_visible_content(self) -> bool:
        """返回实时画布中是否仍有未提交正文。"""
        return bool(
            len(self.blocks) > self.visible_prefix_count
            or self.active_block is not None
            or self._active_tail
        )

    @property
    def visible_blocks(self) -> list[TranscriptBlock]:
        """返回尚未进入滚屏区且未被用户清除的稳定正文块。"""
        return self.blocks[self.visible_prefix_count:]

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

    def _reset_active(self) -> None:
        """重置当前动态正文状态。"""
        self.active_block      = None
        self.active_kind       = None
        self.active_gap_before = False

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

    def stage_submission(self, block: FragmentBlock) -> None:
        """暂存等待命令分派决定展示方式的用户输入。"""
        if self._pending_submission is not None:
            raise RuntimeError("cannot stage multiple TUI submissions")
        self._pending_submission = block

    def commit_submission(self) -> FragmentBlock | None:
        """把暂存用户输入提交为稳定正文块。"""
        block = self._pending_submission
        self._pending_submission = None
        if block is not None:
            self.append_block(block, kind="user")
        return block

    def discard_submission(self) -> bool:
        """丢弃由临时交互表面接管的暂存用户输入。"""
        changed = self._pending_submission is not None
        self._pending_submission = None
        return changed

    def append_block(self, block: FragmentBlock, *, kind: TuiBlockKind) -> bool:
        """追加一个稳定正文块并统一保留块间空行。"""
        item = TranscriptBlock(
            block=block,
            kind=kind,
            gap_before=bool(
                self.blocks
                or self.active_block is not None
                or self._active_tail
            ),
        )
        if self.active_block is not None:
            self._active_tail.append(item)
        else:
            self.blocks.append(item)
        return True

    def discard_trailing_block(self, block: FragmentBlock) -> bool:
        """移除与指定对象相同的末尾稳定正文块。"""
        if (
            self.active_block is not None
            or len(self.blocks) <= self.visible_prefix_count
            or self.blocks[-1].block is not block
        ):
            return False
        self.blocks.pop()
        return True

    def set_active(self, block: FragmentBlock, *, kind: TuiBlockKind) -> None:
        """设置当前动态正文并在首次显示时确定块间空行。"""
        if self.active_block is None:
            self.active_kind = kind
            self.active_gap_before = bool(self.blocks)
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
        self.blocks.extend(self._active_tail)
        self._active_tail.clear()
        self._reset_active()

    def clear_active(self) -> None:
        """清空当前动态正文及其间距状态。"""
        self.blocks.extend(self._active_tail)
        self._active_tail.clear()
        self._reset_active()

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
        blocks.extend(self._active_tail)
        return self._render_blocks(blocks)

    def scrollback_prefix_fragments(self, count: int) -> FormattedText:
        """生成下一批待写入终端滚屏区的稳定正文块。"""
        start = self.visible_prefix_count
        limit = max(0, min(len(self.blocks) - start, int(count)))
        return self._render_blocks(self.blocks[start:start + limit])

    def commit_scrollback_prefix(self, count: int) -> None:
        """推进已经写入终端滚屏区的稳定正文边界。"""
        start     = self.visible_prefix_count
        remaining = len(self.blocks) - start

        self.scrollback_prefix_count = (
            start + max(0, min(remaining, int(count)))
        )

    def clear_visible_prefix(self) -> None:
        """隐藏当前稳定正文并保留完整归档。"""
        self.cleared_prefix_count = len(self.blocks)

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
        blocks.extend(self._active_tail)
        return self._render_blocks(blocks)


if __name__ == '__main__':
    pass
