# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from bisect import (
    bisect_left,
    bisect_right
)
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
    fill_fragments,
    join_formatted_lines,
    sanitize_fragment_block,
    split_formatted_lines
)

if typing.TYPE_CHECKING:
    from mind_app.history.transcript import TranscriptEntry
    from mind_app.presentation.contracts import PresentationView

    TranscriptCellSource: typing.TypeAlias = (
        TranscriptEntry
        | PresentationView
    )
else:
    TranscriptCellSource: typing.TypeAlias = typing.Any

TuiBlockKind = typing.Literal[
    "user",
    "assistant",
    "operation",
    "plan",
    "approval",
    "notice",
    "system"
]

SourceBlockRenderer: typing.TypeAlias = typing.Callable[
    [str, int],
    FragmentBlock
]
WidthBlockRenderer: typing.TypeAlias = typing.Callable[
    [int],
    FragmentBlock
]


@dataclass(frozen=True, slots=True)
class TranscriptBlock(object):
    """保存一项正文的普通表示、完整表示及视觉间距。"""
    display_block: FragmentBlock
    transcript_block: FragmentBlock
    kind: TuiBlockKind
    source: TranscriptCellSource | None = None
    raw_text: str | None = None
    gap_before: int = 0
    stream_continuation: bool = False
    transcript_stable: bool = True
    turn_id: str = ""
    prompt: str = ""
    attachments: tuple[dict[str, typing.Any], ...] = ()
    extras: dict[str, typing.Any] = field(default_factory=dict)
    source_renderer: SourceBlockRenderer | None = None
    source_render_width: int | None = None
    display_renderer: WidthBlockRenderer | None = None
    display_render_width: int | None = None


@dataclass(frozen=True, slots=True)
class TranscriptLiveTail(object):
    """保存仅用于当前画面渲染的动态记录尾部。"""
    cells: tuple[TranscriptBlock, ...]
    revision: int
    stream_continuation: bool


@dataclass(frozen=True, slots=True)
class TranscriptSnapshot(object):
    """保存已提交记录及仅用于渲染的动态尾部。"""
    committed_cells: tuple[TranscriptBlock, ...]
    live_tail: TranscriptLiveTail | None
    committed_revision: int


@dataclass(frozen=True, slots=True)
class TuiDocumentState(object):
    """保存正文提交事务所需的全部可恢复状态。"""
    blocks: tuple[TranscriptBlock, ...]
    scrollback_line_count: int
    cleared_line_count: int
    active_block: FragmentBlock | None
    active_transcript_block: FragmentBlock | None
    active_raw_text: str | None
    active_kind: TuiBlockKind | None
    active_gap_before: int
    active_stream_continuation: bool
    active_transcript_revision: int
    stable_transcript_revision: int
    pending_submission: FragmentBlock | None
    pending_submission_raw_text: str | None
    active_tail: tuple[TranscriptBlock, ...]
    stable_lines: tuple[FormattedText, ...]
    stable_block_end_lines: tuple[int, ...]
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
        self.active_raw_text: str | None                   = None
        self.active_kind: TuiBlockKind | None              = None
        self.active_gap_before: int                        = 0
        self.active_stream_continuation: bool              = False

        self.active_transcript_revision: int = 0
        self.stable_transcript_revision: int = 0

        self._pending_submission: FragmentBlock | None = None
        self._pending_submission_raw_text: str | None  = None

        self._active_tail: list[TranscriptBlock] = []

        self._stable_lines: list[FormattedText]                  = []
        self._stable_block_end_lines: list[int]                  = []
        self._stable_snapshot_cells: tuple[TranscriptBlock, ...] = ()
        self._stable_snapshot_revision: int                      = -1

        self._display_width: int | None       = None
        self._source_layout_width: int | None = None

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
    def stable_line_count(self) -> int:
        """返回全部稳定正文的逻辑行数量。"""
        return self._stable_line_count()

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

    @property
    def visible_tail_kind(self) -> TuiBlockKind | None:
        """返回实时画布最后一个正文 cell 的类型。"""
        if self._active_tail:
            return self._active_tail[-1].kind
        if self.active_block is not None:
            return self.active_kind
        if self.visible_stable_lines():
            return self._last_rendered_kind()
        return None

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
        self.active_block               = None
        self.active_transcript_block    = None
        self.active_raw_text            = None
        self.active_kind                = None
        self.active_gap_before          = 0
        self.active_stream_continuation = False

    def _append_rendered_block(
        self,
        out: FormattedText,
        item: TranscriptBlock,
        *,
        transcript: bool = False,
        leading_content: bool = False,
        leading_content_terminated: bool = False,
        previous_kind: TuiBlockKind | None = None
    ) -> bool:
        """向已有正文追加一个块并统一处理块前间距。"""
        if transcript:
            parts = self._trim_block_fragments(
                list(item.transcript_block.fragments)
            )
        else:
            parts = join_formatted_lines(self._block_lines(item))

        if not parts:
            return False

        if out or leading_content:
            gap_height = (
                0
                if transcript and item.stream_continuation
                else (
                    max(1, int(item.gap_before))
                    if transcript
                    else self._display_gap_height(previous_kind, item)
                )
            )
            if leading_content_terminated and not out:
                separator = "\n" * gap_height
            else:
                separator = "\n" * (gap_height + 1)
            if separator:
                out.append(("", separator))
        out.extend(parts)

        return True

    def _render_blocks(
        self,
        blocks: list[TranscriptBlock],
        *,
        transcript: bool = False,
        leading_content: bool = False,
        leading_content_terminated: bool = False,
        leading_kind: TuiBlockKind | None = None
    ) -> FormattedText:
        """统一渲染一组正文块及其前置间距。"""
        out: FormattedText = []

        previous_kind = leading_kind

        for item in blocks:
            if self._append_rendered_block(
                out,
                item,
                transcript=transcript,
                leading_content=leading_content,
                leading_content_terminated=leading_content_terminated,
                previous_kind=previous_kind,
            ):
                leading_content = False
                leading_content_terminated = False
                previous_kind = item.kind

        return out

    @staticmethod
    def _display_gap_height(
        previous_kind: TuiBlockKind | None,
        item: TranscriptBlock
    ) -> int:
        """返回两个普通正文 cell 之间需要保留的空行数。"""
        if previous_kind == "user" or item.kind == "user":
            return 0
        return max(0, int(item.gap_before))

    def _last_rendered_kind(self) -> TuiBlockKind | None:
        """返回最后一个包含可见内容的稳定 cell 类型。"""
        return next((
            item.kind
            for item in reversed(self.blocks)
            if self._block_lines(item)
        ), None)

    def _stable_line_count(self) -> int:
        """返回全部稳定正文的逻辑行数量。"""
        return len(self._stable_lines)

    def _has_native_scrollback_boundary(self) -> bool:
        """判断当前可见正文前是否保留原生滚屏内容。"""
        return self.scrollback_line_count > self.cleared_line_count

    def _block_lines(self, item: TranscriptBlock) -> list[FormattedText]:
        """返回指定稳定块去除外侧换行后的逻辑行。"""
        block = self._source_rendered_block(
            item,
            width=(
                self._source_layout_width
                if (
                    item.source_renderer is not None
                    or item.display_renderer is not None
                )
                else self._display_width
            ),
            transcript=False,
        )

        parts = self._trim_block_fragments(list(block.fragments))
        lines = split_formatted_lines(parts)

        fill = block.line_fill
        if fill is not None and self._display_width is not None:
            lines = [
                fill_fragments(line, width=self._display_width, fill=fill)
                for line in lines
            ]
        if not lines or item.kind != "user":
            return lines

        return [
            [("", " ")],
            [("", " ")],
            *lines,
            [("", " ")],
            [("", " ")],
        ]

    def _block_start_line(self, block: FragmentBlock) -> int | None:
        """返回指定稳定块首项内容所在的逻辑行位置。"""
        line: int         = 0
        found: int | None = None

        has_rendered_block: bool = False

        previous_kind: TuiBlockKind | None = None

        for item in self.blocks:
            own_lines = self._block_lines(item)
            if not own_lines:
                continue
            if (
                has_rendered_block
                and self._display_gap_height(previous_kind, item)
            ):
                line += self._display_gap_height(previous_kind, item)
            if item.display_block is block:
                found = line
            line += len(own_lines)
            has_rendered_block = True
            previous_kind = item.kind

        return found

    def _rebuild_stable_lines(self) -> None:
        """根据稳定块重新生成逻辑行缓存。"""
        self._stable_lines.clear()
        self._stable_block_end_lines.clear()
        previous_kind: TuiBlockKind | None = None

        for item in self.blocks:
            own_lines = self._block_lines(item)
            if not own_lines:
                continue
            gap_height = self._display_gap_height(previous_kind, item)
            if self._stable_lines and gap_height:
                self._stable_lines.extend([] for _ in range(gap_height))
            self._stable_lines.extend(own_lines)
            self._stable_block_end_lines.append(len(self._stable_lines))
            previous_kind = item.kind

    def set_display_width(
        self,
        width: int,
        *,
        reflow_sources: bool = True,
    ) -> bool:
        """更新正文显示宽度并重建依赖宽度的稳定行。"""
        normalized    = max(1, int(width))
        width_changed = normalized != self._display_width

        has_renderer  = any(
            item.source_renderer is not None
            or item.display_renderer is not None
            for item in self.blocks
        )

        source_reflow = bool(
            has_renderer
            and reflow_sources
            and normalized != self._source_layout_width
        )
        if not width_changed and not source_reflow:
            return False

        self._display_width = normalized
        if not has_renderer or self._source_layout_width is None:
            self._source_layout_width = normalized

        previous_line_count = self._stable_line_count()
        cleared_at_end      = self.cleared_line_count == previous_line_count
        scrollback_at_end   = self.scrollback_line_count == previous_line_count

        if source_reflow:
            self._source_layout_width = normalized

        if source_reflow or (
            width_changed
            and any(
                item.display_block.line_fill is not None
                for item in self.blocks
            )
        ):
            self._rebuild_stable_lines()
            line_count = self._stable_line_count()

            self.cleared_line_count = (
                line_count
                if cleared_at_end
                else min(self.cleared_line_count, line_count)
            )
            self.scrollback_line_count = (
                line_count
                if scrollback_at_end
                else min(self.scrollback_line_count, line_count)
            )
            if source_reflow:
                self.stable_transcript_revision += 1

        return True

    def _extend_stable(self, items: list[TranscriptBlock]) -> None:
        """追加稳定块并让清屏边界跳过新产生的块间距。"""
        if not items:
            return None

        self.stable_transcript_revision += 1

        previous_line_count = self._stable_line_count()
        cleared_at_end      = self.cleared_line_count == previous_line_count
        previous_kind       = self._last_rendered_kind()

        content_start: int | None = None

        for item in items:
            self.blocks.append(item)

            own_lines = self._block_lines(item)
            if not own_lines:
                continue
            gap_height = self._display_gap_height(previous_kind, item)
            if self._stable_lines and gap_height:
                self._stable_lines.extend([] for _ in range(gap_height))
            if content_start is None:
                content_start = len(self._stable_lines)
            self._stable_lines.extend(own_lines)
            self._stable_block_end_lines.append(len(self._stable_lines))
            previous_kind = item.kind

        boundary_lines = max(
            0,
            int(content_start or 0) - previous_line_count,
        )

        if cleared_at_end:
            self.cleared_line_count += boundary_lines

    def stage_submission(
        self,
        block: FragmentBlock,
        *,
        raw_text: str | None = None
    ) -> None:
        """暂存等待命令分派决定展示方式的用户输入。"""
        if self._pending_submission is not None:
            raise RuntimeError("cannot stage multiple TUI submissions")
        self._pending_submission = sanitize_fragment_block(block)
        self._pending_submission_raw_text = (
            str(raw_text) if raw_text is not None else None
        )

    def commit_submission(self) -> FragmentBlock | None:
        """把暂存用户输入提交为稳定正文块。"""
        block    = self._pending_submission
        raw_text = self._pending_submission_raw_text

        self._pending_submission          = None
        self._pending_submission_raw_text = None

        if block is not None:
            self.append_block(block, kind="user", raw_text=raw_text)
        return block

    def discard_submission(self) -> bool:
        """丢弃由临时交互表面接管的暂存用户输入。"""
        changed = self._pending_submission is not None

        self._pending_submission          = None
        self._pending_submission_raw_text = None

        return changed

    def append_block(
        self,
        block: FragmentBlock,
        *,
        kind: TuiBlockKind,
        transcript_block: FragmentBlock | None = None,
        source: TranscriptCellSource | None = None,
        raw_text: str | None = None,
        stream_continuation: bool = False,
        display_renderer: WidthBlockRenderer | None = None,
        display_render_width: int | None = None
    ) -> bool:
        """追加一个稳定正文块并统一保留块间空行。"""
        block = sanitize_fragment_block(block)

        transcript_block = (
            block
            if transcript_block is None
            else sanitize_fragment_block(transcript_block)
        )

        has_prior_content = bool(
            self.blocks
            or self.active_block is not None
            or self._active_tail
        )

        item = TranscriptBlock(
            display_block=block,
            transcript_block=transcript_block,
            kind=kind,
            source=source,
            raw_text=str(raw_text) if raw_text is not None else None,
            gap_before=bool(has_prior_content and not stream_continuation),
            stream_continuation=bool(stream_continuation),
            display_renderer=display_renderer,
            display_render_width=display_render_width,
        )

        if self.active_block is not None:
            self._active_tail.append(item)
            self.active_transcript_revision += 1
        else:
            self._extend_stable([item])

        return True

    def replace_blocks(
        self,
        blocks: typing.Iterable[TranscriptBlock],
    ) -> None:
        """用一组稳定正文块替换当前完整记录。"""
        normalized: list[TranscriptBlock] = []

        for item in blocks:
            normalized.append(replace(
                item,
                display_block=sanitize_fragment_block(item.display_block),
                transcript_block=sanitize_fragment_block(
                    item.transcript_block
                ),
                gap_before=bool(
                    normalized and not item.stream_continuation
                ),
                transcript_stable=True,
                attachments=deepcopy(tuple(item.attachments)),
                extras=deepcopy(dict(item.extras)),
            ))

        self.blocks = normalized
        self.scrollback_line_count = 0
        self.cleared_line_count    = 0

        self._reset_active()

        self._pending_submission          = None
        self._pending_submission_raw_text = None

        self._active_tail.clear()

        self._rebuild_stable_lines()
        self.active_transcript_revision += 1
        self.stable_transcript_revision += 1

        self._stable_snapshot_cells    = ()
        self._stable_snapshot_revision = -1

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

        return self._replace_latest_user(
            lambda item: not item.turn_id,
            turn_id=normalized_turn_id,
            prompt=str(prompt),
        )

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
            active_raw_text=self.active_raw_text,
            active_kind=self.active_kind,
            active_gap_before=self.active_gap_before,
            active_stream_continuation=self.active_stream_continuation,
            active_transcript_revision=self.active_transcript_revision,
            stable_transcript_revision=self.stable_transcript_revision,
            pending_submission=deepcopy(self._pending_submission),
            pending_submission_raw_text=self._pending_submission_raw_text,
            active_tail=deepcopy(tuple(self._active_tail)),
            stable_lines=deepcopy(tuple(self._stable_lines)),
            stable_block_end_lines=tuple(self._stable_block_end_lines),
            stable_snapshot_cells=deepcopy(self._stable_snapshot_cells),
            stable_snapshot_revision=self._stable_snapshot_revision,
        )

    def restore_state(self, state: TuiDocumentState) -> None:
        """恢复正文提交前的可恢复状态。"""
        self.blocks                     = deepcopy(list(state.blocks))
        self.scrollback_line_count      = state.scrollback_line_count
        self.cleared_line_count         = state.cleared_line_count
        self.active_block               = deepcopy(state.active_block)
        self.active_transcript_block    = deepcopy(state.active_transcript_block)
        self.active_raw_text            = state.active_raw_text
        self.active_kind                = state.active_kind
        self.active_gap_before          = state.active_gap_before
        self.active_stream_continuation = state.active_stream_continuation
        self.active_transcript_revision = state.active_transcript_revision
        self.stable_transcript_revision = state.stable_transcript_revision

        self._pending_submission          = deepcopy(state.pending_submission)
        self._pending_submission_raw_text = state.pending_submission_raw_text

        self._active_tail = deepcopy(list(state.active_tail))

        self._stable_lines             = deepcopy(list(state.stable_lines))
        self._stable_block_end_lines   = list(state.stable_block_end_lines)
        self._stable_snapshot_cells    = deepcopy(state.stable_snapshot_cells)
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

        changes: dict[str, typing.Any] = {}
        if attachments is not None:
            changes["attachments"] = tuple(
                deepcopy(dict(value)) for value in attachments
            )
        if extras is not None:
            changes["extras"] = deepcopy(dict(extras))

        return self._replace_latest_user(
            lambda item: item.turn_id == normalized_turn_id,
            **changes,
        )

    def _replace_latest_user(
        self,
        match: typing.Callable[[TranscriptBlock], bool],
        **changes: typing.Any
    ) -> bool:
        """在稳定正文或动态尾部替换最近一条匹配的用户记录。"""
        for items, active in (
            (self._active_tail, True),
            (self.blocks, False),
        ):
            for index in range(len(items) - 1, -1, -1):
                item = items[index]
                if item.kind != "user" or not match(item):
                    continue
                items[index] = replace(item, **changes)
                if active:
                    self.active_transcript_revision += 1
                else:
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
        transcript_block: FragmentBlock | None = None,
        raw_text: str | None = None,
        stream_continuation: bool = False,
        gap_before: int | None = None
    ) -> None:
        """设置当前动态正文并在首次显示时确定块间空行。"""
        block = sanitize_fragment_block(block)

        transcript_block = (
            block
            if transcript_block is None
            else sanitize_fragment_block(transcript_block)
        )

        if self.active_block is None:
            self.active_kind = kind
        elif self.active_kind != kind:
            raise ValueError("active TUI block kind cannot change before commit")

        direct_continuation = bool(
            stream_continuation
            and self._last_rendered_kind() == kind
        )
        if gap_before is not None:
            self.active_gap_before = max(0, int(gap_before))
        else:
            self.active_gap_before = int(bool(
                self.blocks and not direct_continuation
            ))

        self.active_block            = block
        self.active_transcript_block = transcript_block

        self.active_raw_text = (
            str(raw_text) if raw_text is not None else None
        )
        self.active_stream_continuation = bool(stream_continuation)

        self.active_transcript_revision += 1

    def commit_active(
        self,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None = None,
        raw_text: str | None = None,
        source_renderer: SourceBlockRenderer | None = None,
        source_render_width: int | None = None
    ) -> None:
        """把当前动态正文替换为相同位置的稳定块。"""
        if self.active_kind is None:
            raise ValueError("cannot commit an active TUI block without a kind")

        block = sanitize_fragment_block(block)

        transcript_block = (
            block
            if transcript_block is None
            else sanitize_fragment_block(transcript_block)
        )

        items = [TranscriptBlock(
            display_block=block,
            transcript_block=transcript_block,
            kind=self.active_kind,
            raw_text=(
                str(raw_text)
                if raw_text is not None
                else self.active_raw_text
            ),
            gap_before=self.active_gap_before,
            stream_continuation=self.active_stream_continuation,
            source_renderer=source_renderer,
            source_render_width=source_render_width,
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

    def fragments(
        self,
        *,
        width: int,
        reflow_sources: bool = True
    ) -> FormattedText:
        """生成统一处理块边界后的正文片段。"""
        self.set_display_width(width, reflow_sources=reflow_sources)

        out = join_formatted_lines(
            self._stable_lines[self.visible_prefix_line_count:]
        )
        hidden_stable_content = bool(
            not out and self._has_native_scrollback_boundary()
        )

        previous_kind = (
            self._last_rendered_kind()
            if out or hidden_stable_content
            else None
        )

        if self.active_block is not None:
            if self.active_kind is None:
                raise ValueError("active TUI block is missing its semantic kind")
            item = TranscriptBlock(
                display_block=self.active_block,
                transcript_block=self.active_transcript_block or self.active_block,
                kind=self.active_kind,
                raw_text=self.active_raw_text,
                gap_before=self.active_gap_before,
                stream_continuation=self.active_stream_continuation,
                transcript_stable=False,
            )
            if self._append_rendered_block(
                out,
                item,
                leading_content=hidden_stable_content,
                leading_content_terminated=hidden_stable_content,
                previous_kind=previous_kind,
            ):
                hidden_stable_content = False
                previous_kind = item.kind

        for item in self._active_tail:
            if self._append_rendered_block(
                out,
                item,
                leading_content=hidden_stable_content,
                leading_content_terminated=hidden_stable_content,
                previous_kind=previous_kind,
            ):
                hidden_stable_content = False
                previous_kind = item.kind

        return out

    def stable_lines_since(self, line_count: int) -> list[FormattedText]:
        """返回指定累计位置之后追加的稳定正文行。"""
        start = max(0, min(self._stable_line_count(), int(line_count)))
        return self._stable_lines[start:]

    def live_fragments(self) -> FormattedText:
        """生成相对稳定正文追加的动态正文片段。"""
        blocks: list[TranscriptBlock] = []

        if self.active_block is not None:
            if self.active_kind is None:
                raise ValueError("active TUI block is missing its semantic kind")
            blocks.append(TranscriptBlock(
                display_block=self.active_block,
                transcript_block=(
                    self.active_transcript_block or self.active_block
                ),
                kind=self.active_kind,
                raw_text=self.active_raw_text,
                gap_before=self.active_gap_before,
                stream_continuation=self.active_stream_continuation,
                transcript_stable=False,
            ))

        blocks.extend(self._active_tail)
        leading_content = bool(self.visible_stable_lines())
        leading_content_terminated = bool(
            not leading_content
            and self._has_native_scrollback_boundary()
        )

        return self._render_blocks(
            blocks,
            leading_content=(
                leading_content or leading_content_terminated
            ),
            leading_content_terminated=leading_content_terminated,
            leading_kind=(
                self._last_rendered_kind()
                if leading_content or leading_content_terminated
                else None
            ),
        )

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

    def complete_scrollback_prefix_line_count(
        self,
        *,
        required_line_count: int,
        maximum_line_count: int
    ) -> int:
        """返回满足目标且不拆分稳定块的滚屏前缀行数。"""
        start = self.visible_prefix_line_count
        required = max(1, int(required_line_count))
        maximum = max(
            0,
            min(
                self._stable_line_count() - start,
                int(maximum_line_count),
            ),
        )
        if maximum <= 0:
            return 0

        maximum_end  = start + maximum
        required_end = start + required

        target_index = bisect_left(
            self._stable_block_end_lines,
            required_end,
        )

        if (
            target_index < len(self._stable_block_end_lines)
            and self._stable_block_end_lines[target_index] <= maximum_end
        ):
            return self._stable_block_end_lines[target_index] - start

        fallback_index = bisect_right(
            self._stable_block_end_lines,
            maximum_end,
        ) - 1
        if fallback_index < 0:
            return 0

        fallback = self._stable_block_end_lines[fallback_index] - start
        return max(0, fallback)

    def commit_scrollback_prefix(
        self,
        line_count: int,
        *,
        expected_start: int
    ) -> bool:
        """推进已经写入终端滚屏区的稳定逻辑行边界。"""
        start = self.visible_prefix_line_count
        if start != int(expected_start):
            return False

        remaining = self._stable_line_count() - start

        self.scrollback_line_count = (
            start + max(0, min(remaining, int(line_count)))
        )
        return True

    def rewind_scrollback(self, *, max_line_count: int) -> None:
        """把原生滚屏游标回退到最近一段可重新输出的稳定内容。"""
        line_count = self._stable_line_count()

        replay_start = max(
            self.cleared_line_count,
            line_count - max(0, int(max_line_count)),
        )

        self.scrollback_line_count = min(line_count, replay_start)

    def prepare_scrollback_tail(self, *, max_line_count: int) -> None:
        """把当前终端回放范围限制为稳定正文的最近一段。"""
        line_count = self._stable_line_count()

        replay_start = max(
            0,
            line_count - max(0, int(max_line_count)),
        )

        self.scrollback_line_count = replay_start
        self.cleared_line_count    = replay_start

    def restore_scrollback_position(self, line_count: int) -> None:
        """把原生滚屏游标恢复到先前提交位置。"""
        self.scrollback_line_count = max(
            0,
            min(self._stable_line_count(), int(line_count)),
        )

    def clear_visible_prefix(self) -> None:
        """隐藏当前稳定正文并保留完整归档。"""
        self.cleared_line_count = self._stable_line_count()

    def all_fragments(self, *, width: int) -> FormattedText:
        """生成包含已提交前缀在内的完整对话片段。"""
        self.set_display_width(width)
        blocks = list(self.blocks)
        if self.active_block is not None:
            if self.active_kind is None:
                raise ValueError("active TUI block is missing its semantic kind")
            blocks.append(TranscriptBlock(
                display_block=self.active_block,
                transcript_block=self.active_transcript_block or self.active_block,
                kind=self.active_kind,
                raw_text=self.active_raw_text,
                gap_before=self.active_gap_before,
                stream_continuation=self.active_stream_continuation,
                transcript_stable=False,
            ))
        blocks.extend(self._active_tail)
        return self._render_blocks(blocks)

    def transcript_fragments(self, *, width: int) -> FormattedText:
        """生成包含动态正文在内的完整会话记录片段。"""
        _ = width
        snapshot = self.transcript_snapshot()

        committed = self._render_blocks(
            list(snapshot.committed_cells),
            transcript=True,
        )

        live_tail = snapshot.live_tail

        active = self._render_blocks(
            list(live_tail.cells) if live_tail is not None else [],
            transcript=True,
            leading_content=bool(committed),
        )

        return [*committed, *active]

    def transcript_snapshot(self) -> TranscriptSnapshot:
        """返回已提交记录和仅用于渲染的动态尾部快照。"""
        live_cells: list[TranscriptBlock] = []

        if self.active_block is not None:
            if self.active_kind is None:
                raise ValueError("active TUI block is missing its semantic kind")
            live_cells.append(TranscriptBlock(
                display_block=self.active_block,
                transcript_block=self.active_transcript_block or self.active_block,
                kind=self.active_kind,
                raw_text=self.active_raw_text,
                gap_before=self.active_gap_before,
                stream_continuation=self.active_stream_continuation,
                transcript_stable=False,
            ))

        live_cells.extend(self._active_tail)

        if self._stable_snapshot_revision != self.stable_transcript_revision:
            self._stable_snapshot_cells = tuple(self.blocks)
            self._stable_snapshot_revision = self.stable_transcript_revision

        live_tail = (
            TranscriptLiveTail(
                cells=tuple(live_cells),
                revision=self.active_transcript_revision,
                stream_continuation=(
                    live_cells[0].stream_continuation
                    if live_cells
                    else False
                ),
            )
            if live_cells
            else None
        )

        return TranscriptSnapshot(
            committed_cells=self._stable_snapshot_cells,
            live_tail=live_tail,
            committed_revision=self.stable_transcript_revision,
        )

    def transcript_cell_fragments(
        self,
        cell: TranscriptBlock,
        *,
        width: int | None = None
    ) -> FormattedText:
        """返回单个记录 cell 去除外侧换行后的完整片段。"""
        block = self._source_rendered_block(
            cell,
            width=width,
            transcript=True,
        )

        parts = self._trim_block_fragments(list(block.fragments))

        fill = block.line_fill
        if fill is None or width is None:
            return parts

        lines = [
            fill_fragments(line, width=width, fill=fill)
            for line in split_formatted_lines(parts)
        ]

        return join_formatted_lines(lines)

    @staticmethod
    def _source_rendered_block(
        cell: TranscriptBlock,
        *,
        width: int | None,
        transcript: bool
    ) -> FragmentBlock:
        """按需生成指定宽度的正文块。"""
        fallback = (
            cell.transcript_block
            if transcript
            else cell.display_block
        )

        if not transcript and cell.display_renderer is not None:
            if width is None or width == cell.display_render_width:
                return fallback
            return cell.display_renderer(width)

        if (
            width is None
            or cell.source_renderer is None
            or cell.raw_text is None
            or width == cell.source_render_width
        ):
            return fallback

        return cell.source_renderer(cell.raw_text, width)


if __name__ == '__main__':
    pass
