# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from copy import deepcopy
from dataclasses import (
    dataclass,
    field,
    replace
)
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
    """保存一项正文的普通表示、完整表示及视觉间距。"""
    display_block: FragmentBlock
    transcript_block: FragmentBlock
    kind: TuiBlockKind
    gap_before: bool = False
    turn_id: str = ""
    prompt: str = ""
    attachments: tuple[dict[str, typing.Any], ...] = ()
    extras: dict[str, typing.Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TranscriptSnapshot(object):
    """保存稳定记录、动态尾部及各自版本。"""
    stable_cells: tuple[TranscriptBlock, ...]
    active_cells: tuple[TranscriptBlock, ...]
    stable_revision: int
    active_revision: int


@dataclass(frozen=True, slots=True)
class TuiDocumentState(object):
    """保存正文提交事务所需的全部可恢复状态。"""
    blocks: tuple[TranscriptBlock, ...]
    scrollback_line_count: int
    cleared_line_count: int
    active_block: FragmentBlock | None
    active_transcript_block: FragmentBlock | None
    active_kind: TuiBlockKind | None
    active_gap_before: bool
    active_transcript_revision: int
    stable_transcript_revision: int
    pending_submission: FragmentBlock | None
    active_tail: tuple[TranscriptBlock, ...]
    stable_lines: tuple[FormattedText, ...]
    stable_snapshot_cells: tuple[TranscriptBlock, ...]
    stable_snapshot_revision: int


class TuiDocument(object):
    """管理稳定正文、动态正文和全局段间距。"""

    def __init__(self) -> None:
        self.blocks: list[TranscriptBlock] = []
        self.scrollback_line_count: int    = 0
        self.cleared_line_count: int       = 0

        self.active_block: FragmentBlock | None            = None
        self.active_transcript_block: FragmentBlock | None = None
        self.active_kind: TuiBlockKind | None              = None
        self.active_gap_before: bool                       = False

        self.active_transcript_revision: int = 0
        self.stable_transcript_revision: int = 0

        self._pending_submission: FragmentBlock | None = None

        self._active_tail: list[TranscriptBlock] = []

        self._stable_lines: list[FormattedText]                  = []
        self._stable_snapshot_cells: tuple[TranscriptBlock, ...] = ()
        self._stable_snapshot_revision: int                      = -1

    @property
    def has_pending_submission(self) -> bool:
        """返回是否存在尚未决定展示方式的用户输入。"""
        return self._pending_submission is not None

    @property
    def transcript_revision(self) -> int:
        """返回稳定记录与动态尾部的合并版本。"""
        return (
            self.stable_transcript_revision
            + self.active_transcript_revision
        )

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
        self.active_block            = None
        self.active_transcript_block = None
        self.active_kind             = None
        self.active_gap_before       = False

    def _append_rendered_block(
        self,
        out: FormattedText,
        item: TranscriptBlock,
        *,
        transcript: bool = False,
        leading_content: bool = False
    ) -> bool:
        """向已有正文追加一个块并统一处理块前间距。"""
        block = item.transcript_block if transcript else item.display_block
        parts = self._trim_block_fragments(list(block.fragments))

        if not parts:
            return False

        if out or leading_content:
            out.append(("", "\n\n" if item.gap_before else "\n"))
        out.extend(parts)

        return True

    def _render_blocks(
        self,
        blocks: list[TranscriptBlock],
        *,
        transcript: bool = False,
        leading_content: bool = False
    ) -> FormattedText:
        """统一渲染一组正文块及其前置间距。"""
        out: FormattedText = []

        for item in blocks:
            if self._append_rendered_block(
                out,
                item,
                transcript=transcript,
                leading_content=leading_content,
            ):
                leading_content = False

        return out

    def _stable_line_count(self) -> int:
        """返回全部稳定正文的逻辑行数量。"""
        return len(self._stable_lines)

    def _block_lines(self, item: TranscriptBlock) -> list[FormattedText]:
        """返回指定稳定块去除外侧换行后的逻辑行。"""
        parts = self._trim_block_fragments(list(item.display_block.fragments))
        return split_formatted_lines(parts)

    def _block_start_line(self, block: FragmentBlock) -> int | None:
        """返回指定稳定块首项内容所在的逻辑行位置。"""
        line: int         = 0
        found: int | None = None

        has_rendered_block: bool = False

        for item in self.blocks:
            own_lines = self._block_lines(item)
            if not own_lines:
                continue
            if has_rendered_block and item.gap_before:
                line += 1
            if item.display_block is block:
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

        self.stable_transcript_revision += 1

        previous_line_count = self._stable_line_count()
        scrollback_at_end   = self.scrollback_line_count == previous_line_count
        cleared_at_end      = self.cleared_line_count == previous_line_count

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

    def append_block(
        self,
        block: FragmentBlock,
        *,
        kind: TuiBlockKind,
        transcript_block: FragmentBlock | None = None
    ) -> bool:
        """追加一个稳定正文块并统一保留块间空行。"""
        block            = sanitize_fragment_block(block)
        transcript_block = sanitize_fragment_block(transcript_block or block)

        item = TranscriptBlock(
            display_block=block,
            transcript_block=transcript_block,
            kind=kind,
            gap_before=bool(
                self.blocks
                or self.active_block is not None
                or self._active_tail
            ),
        )

        if self.active_block is not None:
            self._active_tail.append(item)
            self.active_transcript_revision += 1
        else:
            self._extend_stable([item])

        return True

    def discard_trailing_block(self, block: FragmentBlock) -> bool:
        """移除与指定对象相同的末尾稳定正文块。"""
        if (
            self.active_block is not None
            or not self.blocks
            or self.blocks[-1].display_block is not block
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
        self.stable_transcript_revision += 1

        line_count = self._stable_line_count()

        self.scrollback_line_count = min(self.scrollback_line_count, line_count)
        self.cleared_line_count    = min(self.cleared_line_count, line_count)

        return True

    def bind_latest_user_turn(
        self,
        turn_id: str,
        prompt: str
    ) -> bool:
        """把最近一条尚未绑定的用户输入关联到模型轮次。"""
        normalized_turn_id = str(turn_id or "").strip()
        if not normalized_turn_id:
            raise ValueError("turn_id is required")
        for index in range(len(self.blocks) - 1, -1, -1):
            item = self.blocks[index]
            if item.kind != "user" or item.turn_id:
                continue
            self.blocks[index] = replace(
                item,
                turn_id=normalized_turn_id,
                prompt=str(prompt),
            )
            self.stable_transcript_revision += 1
            return True

        return False

    def truncate_before_turn(self, turn_id: str) -> bool:
        """移除指定用户轮次及其后的稳定正文。"""
        normalized_turn_id = str(turn_id or "").strip()
        if (
            not normalized_turn_id
            or self.active_block is not None
            or self._active_tail
        ):
            return False

        boundary = self._turn_boundary(normalized_turn_id)
        if boundary is None:
            return False

        del self.blocks[boundary:]

        self._rebuild_stable_lines()
        self.stable_transcript_revision += 1

        self.scrollback_line_count = 0
        self.cleared_line_count    = 0

        return True

    def can_truncate_before_turn(self, turn_id: str) -> bool:
        """判断指定用户轮次是否可在当前稳定状态下截断。"""
        normalized_turn_id = str(turn_id or "").strip()

        return bool(
            normalized_turn_id
            and self.active_block is None
            and not self._active_tail
            and self._turn_boundary(normalized_turn_id) is not None
        )

    def capture_state(self) -> TuiDocumentState:
        """捕获正文在一次提交前的可恢复状态。"""
        return TuiDocumentState(
            blocks=deepcopy(tuple(self.blocks)),
            scrollback_line_count=self.scrollback_line_count,
            cleared_line_count=self.cleared_line_count,
            active_block=deepcopy(self.active_block),
            active_transcript_block=deepcopy(self.active_transcript_block),
            active_kind=self.active_kind,
            active_gap_before=self.active_gap_before,
            active_transcript_revision=self.active_transcript_revision,
            stable_transcript_revision=self.stable_transcript_revision,
            pending_submission=deepcopy(self._pending_submission),
            active_tail=deepcopy(tuple(self._active_tail)),
            stable_lines=deepcopy(tuple(self._stable_lines)),
            stable_snapshot_cells=deepcopy(self._stable_snapshot_cells),
            stable_snapshot_revision=self._stable_snapshot_revision,
        )

    def restore_state(self, state: TuiDocumentState) -> None:
        """恢复正文提交前的可恢复状态。"""
        self.blocks = deepcopy(list(state.blocks))
        self.scrollback_line_count = state.scrollback_line_count
        self.cleared_line_count = state.cleared_line_count
        self.active_block = deepcopy(state.active_block)
        self.active_transcript_block = deepcopy(state.active_transcript_block)
        self.active_kind = state.active_kind
        self.active_gap_before = state.active_gap_before
        self.active_transcript_revision = state.active_transcript_revision
        self.stable_transcript_revision = state.stable_transcript_revision
        self._pending_submission = deepcopy(state.pending_submission)
        self._active_tail = deepcopy(list(state.active_tail))
        self._stable_lines = deepcopy(list(state.stable_lines))
        self._stable_snapshot_cells = deepcopy(state.stable_snapshot_cells)
        self._stable_snapshot_revision = state.stable_snapshot_revision

    def bind_turn_payload(
        self,
        turn_id: str,
        *,
        attachments: typing.Iterable[typing.Mapping[str, typing.Any]] | None = None,
        extras: typing.Mapping[str, typing.Any] | None = None
    ) -> bool:
        """把实际请求附件和扩展输入关联到指定用户轮次。"""
        normalized_turn_id = str(turn_id or "").strip()
        if not normalized_turn_id:
            raise ValueError("turn_id is required")

        for index, item in enumerate(self.blocks):
            if item.kind != "user" or item.turn_id != normalized_turn_id:
                continue
            self.blocks[index] = replace(
                item,
                attachments=(
                    tuple(deepcopy(dict(value)) for value in attachments)
                    if attachments is not None
                    else item.attachments
                ),
                extras=(
                    deepcopy(dict(extras))
                    if extras is not None
                    else item.extras
                ),
            )
            self.stable_transcript_revision += 1
            return True
        return False

    def _turn_boundary(self, turn_id: str) -> int | None:
        """返回指定用户轮次在稳定正文中的位置。"""
        return next((
            index
            for index, item in enumerate(self.blocks)
            if item.kind == "user" and item.turn_id == turn_id
        ), None)

    def set_active(
        self,
        block: FragmentBlock,
        *,
        kind: TuiBlockKind,
        transcript_block: FragmentBlock | None = None
    ) -> None:
        """设置当前动态正文并在首次显示时确定块间空行。"""
        block            = sanitize_fragment_block(block)
        transcript_block = sanitize_fragment_block(transcript_block or block)

        if self.active_block is None:
            self.active_kind = kind
            self.active_gap_before = bool(self.blocks)
        elif self.active_kind != kind:
            raise ValueError("active TUI block kind cannot change before commit")

        self.active_block            = block
        self.active_transcript_block = transcript_block

        self.active_transcript_revision += 1

    def commit_active(
        self,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None
    ) -> None:
        """把当前动态正文替换为相同位置的稳定块。"""
        if self.active_kind is None:
            raise ValueError("cannot commit an active TUI block without a kind")

        block            = sanitize_fragment_block(block)
        transcript_block = sanitize_fragment_block(transcript_block or block)

        items = [TranscriptBlock(
            display_block=block,
            transcript_block=transcript_block,
            kind=self.active_kind,
            gap_before=self.active_gap_before,
        ), *self._active_tail]

        self._extend_stable(items)
        self._active_tail.clear()
        self._reset_active()
        self.active_transcript_revision += 1

    def clear_active(self) -> None:
        """清空当前动态正文及其间距状态。"""
        changed = bool(self.active_block is not None or self._active_tail)

        self._extend_stable(self._active_tail)
        self._active_tail.clear()
        self._reset_active()

        if changed:
            self.active_transcript_revision += 1

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
                display_block=self.active_block,
                transcript_block=self.active_transcript_block or self.active_block,
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
                display_block=self.active_block,
                transcript_block=self.active_transcript_block or self.active_block,
                kind=self.active_kind,
                gap_before=self.active_gap_before,
            ))
        blocks.extend(self._active_tail)
        return self._render_blocks(blocks)

    def transcript_fragments(self, *, width: int) -> FormattedText:
        """生成包含动态正文在内的完整会话记录片段。"""
        _ = width
        snapshot = self.transcript_snapshot()

        stable = self._render_blocks(
            list(snapshot.stable_cells),
            transcript=True,
        )

        active = self._render_blocks(
            list(snapshot.active_cells),
            transcript=True,
            leading_content=bool(stable),
        )

        return [*stable, *active]

    def transcript_snapshot(self) -> TranscriptSnapshot:
        """返回当前完整记录使用的不可变 cell 快照。"""
        active_cells: list[TranscriptBlock] = []
        if self.active_block is not None:
            if self.active_kind is None:
                raise ValueError("active TUI block is missing its semantic kind")
            active_cells.append(TranscriptBlock(
                display_block=self.active_block,
                transcript_block=self.active_transcript_block or self.active_block,
                kind=self.active_kind,
                gap_before=self.active_gap_before,
            ))
        active_cells.extend(self._active_tail)
        if self._stable_snapshot_revision != self.stable_transcript_revision:
            self._stable_snapshot_cells = tuple(self.blocks)
            self._stable_snapshot_revision = self.stable_transcript_revision
        return TranscriptSnapshot(
            stable_cells=self._stable_snapshot_cells,
            active_cells=tuple(active_cells),
            stable_revision=self.stable_transcript_revision,
            active_revision=self.active_transcript_revision,
        )

    def transcript_cell_fragments(
        self,
        cell: TranscriptBlock,
    ) -> FormattedText:
        """返回单个记录 cell 去除外侧换行后的完整片段。"""
        return self._trim_block_fragments(list(cell.transcript_block.fragments))


if __name__ == '__main__':
    pass
