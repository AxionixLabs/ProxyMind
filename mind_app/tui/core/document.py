# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from .models import (
    FormattedText,
    FragmentBlock
)
from .render import (
    join_formatted_lines,
    sanitize_fragment_block,
    split_formatted_lines
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
        self.blocks: list[TranscriptBlock]      = []
        self.scrollback_line_count: int         = 0
        self.cleared_line_count: int            = 0
        self.active_block: FragmentBlock | None = None
        self.active_kind: TuiBlockKind | None   = None
        self.active_gap_before: bool            = False

        self._active_tail: list[TranscriptBlock]       = []
        self._pending_submission: FragmentBlock | None = None
        self._stable_lines: list[FormattedText]        = []

    @property
    def has_pending_submission(self) -> bool:
        """返回是否存在尚未决定展示方式的用户输入。"""
        return self._pending_submission is not None

    @property
    def visible_prefix_line_count(self) -> int:
        """返回实时正文应跳过的稳定逻辑行数量。"""
        return max(
            self.scrollback_line_count,
            self.cleared_line_count,
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
    def has_conversation(self) -> bool:
        """返回归档中是否存在用户或助手对话。"""
        conversation_kinds = {"user", "assistant"}
        return bool(
            self.active_kind in conversation_kinds
            or any(
                item.kind in conversation_kinds
                for item in (*self.blocks, *self._active_tail)
            )
        )

    @property
    def has_visible_content(self) -> bool:
        """返回实时画布中是否仍有未提交正文。"""
        return bool(
            self._stable_line_count() > self.visible_prefix_line_count
            or self.active_block is not None
            or self._active_tail
        )

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

    def _append_rendered_block(
        self,
        out: FormattedText,
        item: TranscriptBlock,
    ) -> None:
        """向已有正文追加一个块并统一处理块前间距。"""
        parts = self._trim_block_fragments(list(item.block.fragments))
        if not parts:
            return None
        if out:
            out.append(("", "\n\n" if item.gap_before else "\n"))
        out.extend(parts)

    def _render_blocks(self, blocks: list[TranscriptBlock]) -> FormattedText:
        """统一渲染一组正文块及其前置间距。"""
        out: FormattedText = []
        for item in blocks:
            self._append_rendered_block(out, item)
        return out

    def _stable_line_count(self) -> int:
        """返回全部稳定正文的逻辑行数量。"""
        return len(self._stable_lines)

    def _block_lines(self, item: TranscriptBlock) -> list[FormattedText]:
        """返回指定稳定块去除外侧换行后的逻辑行。"""
        parts = self._trim_block_fragments(list(item.block.fragments))
        return split_formatted_lines(parts)

    def _block_start_line(self, block: FragmentBlock) -> int | None:
        """返回指定稳定块首项内容所在的逻辑行位置。"""
        line: int                = 0
        found: int | None        = None
        has_rendered_block: bool = False

        for item in self.blocks:
            own_lines = self._block_lines(item)
            if not own_lines:
                continue
            if has_rendered_block and item.gap_before:
                line += 1
            if item.block is block:
                found = line
            line += len(own_lines)
            has_rendered_block = True
        return found

    def _rebuild_stable_lines(self) -> None:
        """根据稳定块重新生成逻辑行缓存。"""
        self._stable_lines.clear()

        for item in self.blocks:
            own_lines = self._block_lines(item)
            if not own_lines:
                continue
            if self._stable_lines and item.gap_before:
                self._stable_lines.append([])
            self._stable_lines.extend(own_lines)

    def _extend_stable(self, items: list[TranscriptBlock]) -> None:
        """追加稳定块并让已隐藏边界跳过新产生的块间距。"""
        if not items:
            return None

        previous_line_count = self._stable_line_count()
        scrollback_at_end = self.scrollback_line_count == previous_line_count
        cleared_at_end    = self.cleared_line_count == previous_line_count

        content_start: int | None = None

        for item in items:
            self.blocks.append(item)

            own_lines = self._block_lines(item)
            if not own_lines:
                continue
            if self._stable_lines and item.gap_before:
                self._stable_lines.append([])
            if content_start is None:
                content_start = len(self._stable_lines)
            self._stable_lines.extend(own_lines)

        boundary_lines = max(
            0,
            int(content_start or 0) - previous_line_count,
        )

        if scrollback_at_end:
            self.scrollback_line_count += boundary_lines
        if cleared_at_end:
            self.cleared_line_count += boundary_lines

    def stage_submission(self, block: FragmentBlock) -> None:
        """暂存等待命令分派决定展示方式的用户输入。"""
        if self._pending_submission is not None:
            raise RuntimeError("cannot stage multiple TUI submissions")
        self._pending_submission = sanitize_fragment_block(block)

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
        block = sanitize_fragment_block(block)

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
            self._extend_stable([item])

        return True

    def discard_trailing_block(self, block: FragmentBlock) -> bool:
        """移除与指定对象相同的末尾稳定正文块。"""
        if (
            self.active_block is not None
            or not self.blocks
            or self.blocks[-1].block is not block
        ):
            return False

        block_start = self._block_start_line(block)
        if (
            block_start is None
            or self.visible_prefix_line_count > block_start
        ):
            return False

        self.blocks.pop()
        self._rebuild_stable_lines()

        line_count = self._stable_line_count()

        self.scrollback_line_count = min(self.scrollback_line_count, line_count)
        self.cleared_line_count    = min(self.cleared_line_count, line_count)

        return True

    def set_active(self, block: FragmentBlock, *, kind: TuiBlockKind) -> None:
        """设置当前动态正文并在首次显示时确定块间空行。"""
        block = sanitize_fragment_block(block)

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

        block = sanitize_fragment_block(block)

        items = [TranscriptBlock(
            block=block,
            kind=self.active_kind,
            gap_before=self.active_gap_before,
        ), *self._active_tail]

        self._extend_stable(items)
        self._active_tail.clear()
        self._reset_active()

    def clear_active(self) -> None:
        """清空当前动态正文及其间距状态。"""
        self._extend_stable(self._active_tail)
        self._active_tail.clear()
        self._reset_active()

    def fragments(self, *, width: int) -> FormattedText:
        """生成统一处理块边界后的正文片段。"""
        _ = width
        out = join_formatted_lines(
            self._stable_lines[self.visible_prefix_line_count:]
        )

        if self.active_block is not None:
            if self.active_kind is None:
                raise ValueError("active TUI block is missing its semantic kind")
            self._append_rendered_block(out, TranscriptBlock(
                block=self.active_block,
                kind=self.active_kind,
                gap_before=self.active_gap_before,
            ))

        for item in self._active_tail:
            self._append_rendered_block(out, item)

        return out

    def visible_stable_lines(self) -> list[FormattedText]:
        """返回尚未进入滚屏区且未被清除的稳定逻辑行。"""
        return self._stable_lines[self.visible_prefix_line_count:]

    def visible_line_offset_for_block(self, block: FragmentBlock) -> int | None:
        """返回指定稳定块首项内容相对实时正文的逻辑行位置。"""
        line = self._block_start_line(block)
        if line is None or line < self.visible_prefix_line_count:
            return None
        return line - self.visible_prefix_line_count

    def scrollback_prefix_fragments(self, line_count: int) -> FormattedText:
        """生成下一批待写入终端滚屏区的稳定逻辑行。"""
        start = self.visible_prefix_line_count
        limit = max(0, min(len(self._stable_lines) - start, int(line_count)))
        return join_formatted_lines(self._stable_lines[start:start + limit])

    def commit_scrollback_prefix(self, line_count: int) -> None:
        """推进已经写入终端滚屏区的稳定逻辑行边界。"""
        start     = self.visible_prefix_line_count
        remaining = self._stable_line_count() - start

        self.scrollback_line_count = (
            start + max(0, min(remaining, int(line_count)))
        )

    def clear_visible_prefix(self) -> None:
        """隐藏当前稳定正文并保留完整归档。"""
        self.cleared_line_count = self._stable_line_count()

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
