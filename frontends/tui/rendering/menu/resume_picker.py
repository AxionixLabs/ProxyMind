# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from dataclasses import (
    dataclass,
    replace
)
from enum import Enum
from prompt_toolkit.utils import get_cwidth
from frontends.tui.contracts.resume import (
    ResumeArchiveStatus,
    ResumeDensity,
    ResumeFilterMode,
    ResumePickerRequest,
    ResumePreview,
    ResumePreviewStatus,
    ResumeRow,
    ResumeSessionStatus,
    ResumeSortKey
)
from frontends.tui.contracts.text import FormattedText
from ..fragments import (
    clip_fragments,
    clip_text,
    fragments_text,
    join_formatted_lines,
    wrap_formatted_lines
)
from .layout import (
    MENU_SURFACE_HORIZONTAL_INSET,
    surface_content_width
)
from ..screen.overlays import (
    transcript_header_fragments,
    transcript_separator_fragments
)
from ..text_sanitize import sanitize_formatted_text

PICKER_CHROME_HEIGHT: typing.Final[int]         = 8
PICKER_FOOTER_HEIGHT: typing.Final[int]         = 4
SESSION_META_INDENT_WIDTH: typing.Final[int]    = 2
SESSION_META_DATE_WIDTH: typing.Final[int]      = 12
SESSION_META_FIELD_GAP_WIDTH: typing.Final[int] = 2
SESSION_META_MIN_CWD_WIDTH: typing.Final[int]   = 30
SESSION_META_MAX_CWD_WIDTH: typing.Final[int]   = 72
SESSION_META_CWD_ICON: typing.Final[str]        = "⌁"
SESSION_META_BRANCH_ICON: typing.Final[str]     = ""


class ResumeToolbarFocus(str, Enum):
    """描述 toolbar 当前接收左右键的控件。"""
    FILTER = "filter"
    STATUS = "status"
    SORT = "sort"


@dataclass(frozen=True, slots=True)
class ResumePickerState(object):
    """保存单次 picker 会话可由纯函数转换的交互状态。"""
    request: ResumePickerRequest
    query: str
    selected: int
    scroll_top: int
    filter_mode: ResumeFilterMode
    status: ResumeSessionStatus
    sort_key: ResumeSortKey
    density: ResumeDensity
    toolbar_focus: ResumeToolbarFocus
    expanded_row_key: tuple[str, str] | None
    preview: ResumePreview | None
    generation: int
    relative_time_now_ms: int
    transcript_mode: bool
    transcript_scroll_top: int
    transcript_follow_bottom: bool
    archive_status: ResumeArchiveStatus = ResumeArchiveStatus.IDLE
    archive_row_key: tuple[str, str] | None = None
    archive_error: str | None = None


def create_resume_picker_state(
    request: ResumePickerRequest,
    *,
    generation: int = 0,
    now_ms: int | None = None,
) -> ResumePickerState:
    """按请求创建具有稳定相对时间基准的 picker 状态。"""
    filter_mode = request.initial_filter
    if not request.filter_workspace:
        filter_mode = ResumeFilterMode.ALL
    state = ResumePickerState(
        request=request,
        query="",
        selected=0,
        scroll_top=0,
        filter_mode=filter_mode,
        status=request.initial_status,
        sort_key=request.initial_sort,
        density=request.initial_density,
        toolbar_focus=ResumeToolbarFocus.FILTER,
        expanded_row_key=None,
        preview=None,
        generation=max(0, int(generation)),
        relative_time_now_ms=(
            int(time.time() * 1000) if now_ms is None else int(now_ms)
        ),
        transcript_mode=False,
        transcript_scroll_top=0,
        transcript_follow_bottom=True,
    )
    return _clamp_selection(state)


def begin_resume_archive(
    state: ResumePickerState,
    row: ResumeRow,
    *,
    restoring: bool
) -> ResumePickerState:
    """标记一次带 picker 生命周期的归档或恢复操作。"""
    return replace(
        state,
        archive_status=(
            ResumeArchiveStatus.RESTORING
            if restoring
            else ResumeArchiveStatus.PENDING
        ),
        archive_row_key=row.key,
        archive_error=None,
    )


def finish_resume_archive(
    state: ResumePickerState,
    *,
    row_key: tuple[str, str],
    error: str | None = None
) -> ResumePickerState:
    """结束当前归档动作并在失败时保留错误提示。"""
    if state.archive_row_key != row_key:
        return state
    return replace(
        state,
        archive_status=ResumeArchiveStatus.IDLE,
        archive_row_key=None,
        archive_error=(str(error).strip() or None) if error else None,
    )


def remove_resume_row(
    state: ResumePickerState,
    *,
    row_key: tuple[str, str]
) -> ResumePickerState:
    """从当前 picker 快照中移除已成功归档的会话。"""
    if state.archive_row_key != row_key:
        return state
    rows = tuple(row for row in state.request.rows if row.key != row_key)
    return _clamp_selection(replace(
        state,
        request=replace(state.request, rows=rows),
        archive_status=ResumeArchiveStatus.IDLE,
        archive_row_key=None,
        archive_error=None,
        expanded_row_key=(
            None if state.expanded_row_key == row_key else state.expanded_row_key
        ),
        preview=(
            None
            if state.preview is not None and state.preview.row_key == row_key
            else state.preview
        ),
    ))


def filtered_resume_rows(state: ResumePickerState) -> tuple[ResumeRow, ...]:
    """返回按当前 query、工作区和排序条件生成的稳定行快照。"""
    query     = state.query.casefold().strip()
    workspace = _workspace_key(state.request.filter_workspace)

    rows = (
        row
        for row in state.request.rows
        if (
            state.filter_mode is ResumeFilterMode.ALL
            or not workspace
            or _workspace_key(row.workspace) == workspace
        )
        and row.status == state.status
        and (not query or _row_matches_query(row, query))
    )
    return tuple(sorted(rows, key=lambda row: _sort_key(state, row), reverse=True))


def selected_resume_row(state: ResumePickerState) -> ResumeRow | None:
    """返回当前筛选结果中的选中行。"""
    rows = filtered_resume_rows(state)
    if not rows:
        return None
    return rows[min(max(0, state.selected), len(rows) - 1)]


def set_resume_query(state: ResumePickerState, query: str) -> ResumePickerState:
    """替换搜索文本并把列表定位重置到首项。"""
    value = _safe_text(query).replace("\r\n", " ").replace("\r", " ")
    value = value.replace("\n", " ")
    return _clamp_selection(replace(
        state,
        query=value,
        selected=0,
        scroll_top=0,
        expanded_row_key=None,
        preview=None,
        transcript_mode=False,
        transcript_scroll_top=0,
        transcript_follow_bottom=True,
    ))


def append_resume_query(state: ResumePickerState, text: str) -> ResumePickerState:
    """向搜索文本追加一次键入或粘贴内容。"""
    value = str(text or "").replace("\r\n", " ").replace("\r", " ")
    value = value.replace("\n", " ")
    if not value:
        return state
    separator = "" if not state.query or state.query[-1].isspace() else " "
    return set_resume_query(state, f"{state.query}{separator}{value}")


def backspace_resume_query(state: ResumePickerState) -> ResumePickerState:
    """删除搜索文本的最后一个字符。"""
    return set_resume_query(state, state.query[:-1])


def delete_resume_query_word(state: ResumePickerState) -> ResumePickerState:
    """删除搜索文本末尾的空白和前一个词。"""
    value = state.query.rstrip()
    if not value:
        return set_resume_query(state, "")
    boundary = value.rfind(" ")
    return set_resume_query(state, value[:boundary + 1] if boundary >= 0 else "")


def focus_resume_toolbar(
    state: ResumePickerState,
    *,
    reverse: bool = False
) -> ResumePickerState:
    """在 Filter、Status 和 Sort 控件之间移动焦点。"""
    controls = (
        ResumeToolbarFocus.FILTER,
        ResumeToolbarFocus.STATUS,
        ResumeToolbarFocus.SORT,
    )
    current = controls.index(state.toolbar_focus)
    focus = controls[(current + (-1 if reverse else 1)) % len(controls)]
    return replace(state, toolbar_focus=focus)


def change_resume_toolbar_value(state: ResumePickerState) -> ResumePickerState:
    """切换当前 toolbar 控件的值。"""
    if state.toolbar_focus is ResumeToolbarFocus.FILTER:
        if not state.request.filter_workspace:
            return state
        mode = (
            ResumeFilterMode.ALL
            if state.filter_mode is ResumeFilterMode.CWD
            else ResumeFilterMode.CWD
        )
        return _clamp_selection(replace(
            state,
            filter_mode=mode,
            selected=0,
            scroll_top=0,
            expanded_row_key=None,
            preview=None,
        ))

    if state.toolbar_focus is ResumeToolbarFocus.STATUS:
        status = (
            ResumeSessionStatus.ARCHIVED
            if state.status is ResumeSessionStatus.ACTIVE
            else ResumeSessionStatus.ACTIVE
        )
        return _clamp_selection(replace(
            state,
            status=status,
            selected=0,
            scroll_top=0,
            expanded_row_key=None,
            preview=None,
        ))

    selected_key = (
        selected_resume_row(state).key
        if selected_resume_row(state) is not None
        else None
    )
    sort_key = (
        ResumeSortKey.CREATED
        if state.sort_key is ResumeSortKey.UPDATED
        else ResumeSortKey.UPDATED
    )
    updated = replace(state, sort_key=sort_key, scroll_top=0)
    if selected_key is None:
        return _clamp_selection(updated)
    for index, row in enumerate(filtered_resume_rows(updated)):
        if row.key == selected_key:
            return replace(updated, selected=index)
    return _clamp_selection(replace(updated, selected=0))


def move_resume_selection(
    state: ResumePickerState,
    action: typing.Literal["up", "down", "home", "end", "page_up", "page_down"],
    *,
    viewport_rows: int,
    width: int
) -> ResumePickerState:
    """按列表动作移动选中项并保证其仍在 viewport 内。"""
    rows = filtered_resume_rows(state)
    if not rows:
        return replace(state, selected=0, scroll_top=0)

    selected = min(max(0, state.selected), len(rows) - 1)
    page_size = max(
        1,
        int(viewport_rows) // (
            3 if state.density is ResumeDensity.COMFORTABLE else 1
        ),
    )
    if action == "up":
        selected -= 1
    elif action == "down":
        selected += 1
    elif action == "home":
        selected = 0
    elif action == "end":
        selected = len(rows) - 1
    elif action == "page_up":
        selected -= page_size
    elif action == "page_down":
        selected += page_size

    selected = min(max(0, selected), len(rows) - 1)
    updated = replace(
        state,
        selected=selected,
        expanded_row_key=(
            state.expanded_row_key
            if rows[selected].key == state.expanded_row_key
            else None
        ),
        preview=(
            state.preview
            if state.preview is not None
            and state.preview.row_key == rows[selected].key
            else None
        ),
    )
    return ensure_resume_selection_visible(
        updated,
        viewport_rows=viewport_rows,
        width=width,
    )


def ensure_resume_selection_visible(
    state: ResumePickerState,
    *,
    viewport_rows: int,
    width: int
) -> ResumePickerState:
    """调整 scroll_top 使选中行在当前列表高度内可见。"""
    rows = filtered_resume_rows(state)
    if not rows:
        return replace(state, selected=0, scroll_top=0)

    selected   = min(max(0, state.selected), len(rows) - 1)
    scroll_top = min(max(0, state.scroll_top), selected)
    available  = max(1, int(viewport_rows) - int(scroll_top > 0))

    while scroll_top < selected:
        height = _rendered_height_between(
            state,
            rows,
            scroll_top,
            selected,
            width=surface_content_width(
                width,
                inset=MENU_SURFACE_HORIZONTAL_INSET,
            ),
        )
        if height <= available:
            break
        scroll_top += 1
        available = max(1, int(viewport_rows) - 1)

    return replace(state, selected=selected, scroll_top=scroll_top)


def toggle_resume_density(
    state: ResumePickerState,
    *,
    viewport_rows: int,
    width: int
) -> ResumePickerState:
    """切换 dense/comfortable 视图并维持当前选择。"""
    density = (
        ResumeDensity.COMFORTABLE
        if state.density is ResumeDensity.DENSE
        else ResumeDensity.DENSE
    )
    return ensure_resume_selection_visible(
        replace(state, density=density),
        viewport_rows=viewport_rows,
        width=width,
    )


def toggle_resume_expansion(state: ResumePickerState) -> ResumePickerState:
    """展开或收起当前行的详细信息。"""
    row = selected_resume_row(state)
    if row is None:
        return state
    expanded = None if state.expanded_row_key == row.key else row.key
    return replace(
        state,
        expanded_row_key=expanded,
        preview=state.preview if expanded is not None else None,
    )


def set_resume_preview(
    state: ResumePickerState,
    preview: ResumePreview | None
) -> ResumePickerState:
    """只把属于当前选中行的 preview 写入状态。"""
    row = selected_resume_row(state)
    if preview is None:
        return replace(state, preview=None)
    if row is None or preview.row_key != row.key:
        return state
    return replace(
        state,
        expanded_row_key=None if state.transcript_mode else row.key,
        preview=preview,
    )


def enter_resume_transcript(state: ResumePickerState) -> ResumePickerState:
    """进入当前选中会话的全屏 transcript pager。"""
    return replace(
        state,
        transcript_mode=True,
        transcript_scroll_top=0,
        transcript_follow_bottom=True,
        expanded_row_key=None,
    )


def exit_resume_transcript(state: ResumePickerState) -> ResumePickerState:
    """退出全屏 transcript pager 并恢复 picker 列表。"""
    return replace(
        state,
        transcript_mode=False,
        transcript_scroll_top=0,
        transcript_follow_bottom=True,
        preview=None,
    )


def move_resume_transcript(
    state: ResumePickerState,
    action: typing.Literal["up", "down", "home", "end", "page_up", "page_down"],
    *,
    width: int,
    height: int
) -> ResumePickerState:
    """按 pager 动作移动完整 transcript 的可见窗口。"""
    if not state.transcript_mode:
        return state
    lines      = _resume_transcript_lines(state, width=max(1, int(width)))
    viewport   = _resume_transcript_body_height(max(1, int(height)))
    max_scroll = max(0, len(lines) - viewport)
    current    = min(max(0, state.transcript_scroll_top), max_scroll)
    page       = max(1, viewport - 1)

    if action == "up":
        current -= 1
    elif action == "down":
        current += 1
    elif action == "home":
        current = 0
    elif action == "end":
        current = max_scroll
    elif action == "page_up":
        current -= page
    elif action == "page_down":
        current += page
    current = min(max(0, current), max_scroll)
    return replace(
        state,
        transcript_scroll_top=current,
        transcript_follow_bottom=current >= max_scroll,
    )


def resume_picker_list_height(height: int) -> int:
    """返回扣除固定 chrome 后的列表显示高度。"""
    return max(0, int(height) - PICKER_CHROME_HEIGHT)


def render_resume_picker(
    state: ResumePickerState,
    *,
    width: int,
    height: int
) -> FormattedText:
    """生成不超过当前终端宽高的完整 Resume picker 画布。"""
    canvas_width  = max(1, int(width))
    canvas_height = max(1, int(height))

    if state.transcript_mode:
        return render_resume_transcript(
            state,
            width=canvas_width,
            height=canvas_height,
        )

    list_height = resume_picker_list_height(canvas_height)

    lines: list[FormattedText] = [
        _chrome_line([
            ("class:resume-picker.title", "Resume a previous session"),
        ], width=canvas_width),
        _blank_line(canvas_width),
        _search_line(state, width=canvas_width),
        _blank_line(canvas_width),
    ]
    lines.extend(_list_lines(
        state,
        width=canvas_width,
        height=list_height,
    ))
    lines.extend(_footer_lines(
        state,
        width=canvas_width,
        list_height=list_height,
    ))

    lines = lines[:canvas_height]
    while len(lines) < canvas_height:
        lines.append(_blank_line(canvas_width))
    return join_formatted_lines(lines)


def render_resume_transcript(
    state: ResumePickerState,
    *,
    width: int,
    height: int
) -> FormattedText:
    """生成全屏 transcript pager 画布。"""
    canvas_width  = max(1, int(width))
    canvas_height = max(1, int(height))
    body_height   = _resume_transcript_body_height(canvas_height)
    content       = _resume_transcript_lines(state, width=canvas_width)
    max_scroll    = max(0, len(content) - body_height)

    scroll_top = (
        max_scroll
        if state.transcript_follow_bottom
        else min(max(0, state.transcript_scroll_top), max_scroll)
    )

    visible = content[scroll_top:scroll_top + body_height]

    while len(visible) < body_height:
        visible.append([(
            "class:transcript.overlay.filler",
            "~",
        )])

    percentage = 100 if max_scroll == 0 else round(100 * scroll_top / max_scroll)

    lines: list[FormattedText] = [
        _fit_line(
            transcript_header_fragments(
                width=canvas_width,
                raw_mode=False,
            ),
            width=canvas_width,
        ),
        *(_fit_line(line, width=canvas_width) for line in visible),
        _fit_line(
            transcript_separator_fragments(
                width=canvas_width,
                percentage=percentage,
            ),
            width=canvas_width,
        ),
        _fit_line([
            ("class:transcript.overlay.help", " ↑/↓ to scroll   pgup/pgdn to page   home/end to jump"),
        ], width=canvas_width),
        _fit_line([
            ("class:transcript.overlay.help", " q to quit   esc to edit prev"),
        ], width=canvas_width),
    ]

    return join_formatted_lines(lines[:canvas_height])


def _resume_transcript_body_height(height: int) -> int:
    """返回全屏 transcript 扣除标题、分隔线和提示后的正文高度。"""
    return max(1, int(height) - 4)


def _resume_transcript_lines(
    state: ResumePickerState,
    *,
    width: int
) -> list[FormattedText]:
    """把当前 transcript preview 转换为可换行的全屏正文行。"""
    preview = state.preview
    if preview is None or preview.status is ResumePreviewStatus.LOADING:
        return [[
            ("class:transcript.overlay.help", "Loading transcript..."),
        ]]
    if preview.status is ResumePreviewStatus.ERROR:
        return [[
            (
                "class:transcript.overlay.export-error",
                _safe_text(preview.error) or "Could not load transcript",
            ),
        ]]
    lines: list[FormattedText] = []
    for block_index, block in enumerate(preview.blocks):
        if block_index and lines:
            lines.append([])
        fragments = sanitize_formatted_text(list(block))
        lines.extend(wrap_formatted_lines(fragments, width=max(1, int(width))))
    if not lines:
        return [[
            ("class:transcript.overlay.help", "No transcript available"),
        ]]
    return lines


def _search_line(state: ResumePickerState, *, width: int) -> FormattedText:
    available = max(0, width - 2)
    query     = _safe_text(state.query)

    search: FormattedText = [(
        "class:resume-picker.search" if query else (
            "class:resume-picker.search.placeholder"
        ),
        f"Search: {query}" if query else "Type to search",
    )]

    toolbar = _toolbar_fragments(state, compact=False)

    if _fragments_width(search) + _fragments_width(toolbar) + 2 > available:
        toolbar = _toolbar_fragments(state, compact=True)
        compact_budget = max(
            0,
            available - min(available, get_cwidth("Type to search")) - 2,
        )
        if _fragments_width(toolbar) > compact_budget:
            toolbar_without_status = _toolbar_fragments(
                state,
                compact=True,
                include_status=False,
            )
            if _fragments_width(toolbar_without_status) <= compact_budget:
                toolbar = toolbar_without_status

    minimum_search_width = min(available, get_cwidth("Type to search"))
    toolbar_width = min(
        _fragments_width(toolbar),
        max(0, available - minimum_search_width - 2),
    )
    toolbar = clip_fragments(toolbar, width=toolbar_width)
    search_width  = max(0, available - toolbar_width - 2)

    search = clip_fragments(search, width=search_width)
    spacer = max(0, available - _fragments_width(search) - toolbar_width)
    parts  = [*search, ("", " " * spacer), *toolbar]

    return _chrome_line(parts, width=width)


def _toolbar_fragments(
    state: ResumePickerState,
    *,
    compact: bool,
    include_status: bool = True,
) -> FormattedText:
    separator = " " if compact else "   "
    parts: FormattedText = []
    if compact or not state.request.filter_workspace:
        parts.extend([
            ("class:resume-picker.toolbar", "Filter:"),
            _toolbar_value(
                "Cwd" if state.filter_mode is ResumeFilterMode.CWD else "All",
                active=True,
                focused=state.toolbar_focus is ResumeToolbarFocus.FILTER,
            ),
        ])
    else:
        parts.extend([
            ("class:resume-picker.toolbar", "Filter: "),
            _toolbar_value(
                "Cwd",
                active=state.filter_mode is ResumeFilterMode.CWD,
                focused=state.toolbar_focus is ResumeToolbarFocus.FILTER,
            ),
            _toolbar_value(
                "All",
                active=state.filter_mode is ResumeFilterMode.ALL,
                focused=state.toolbar_focus is ResumeToolbarFocus.FILTER,
            ),
        ])
    if include_status:
        parts.append(("class:resume-picker.toolbar", separator))
        if compact:
            parts.extend([
                ("class:resume-picker.toolbar", "Status:"),
                _toolbar_value(
                    "Active" if state.status is ResumeSessionStatus.ACTIVE else "Archived",
                    active=True,
                    focused=state.toolbar_focus is ResumeToolbarFocus.STATUS,
                ),
            ])
        else:
            parts.extend([
                ("class:resume-picker.toolbar", "Status: "),
                _toolbar_value(
                    "Active",
                    active=state.status is ResumeSessionStatus.ACTIVE,
                    focused=state.toolbar_focus is ResumeToolbarFocus.STATUS,
                ),
                _toolbar_value(
                    "Archived",
                    active=state.status is ResumeSessionStatus.ARCHIVED,
                    focused=state.toolbar_focus is ResumeToolbarFocus.STATUS,
                ),
            ])
    parts.append(("class:resume-picker.toolbar", separator))
    parts.append(("class:resume-picker.toolbar", separator))
    if compact:
        parts.extend([
            ("class:resume-picker.toolbar", "Sort:"),
            _toolbar_value(
                "Updated" if state.sort_key is ResumeSortKey.UPDATED else "Created",
                active=True,
                focused=state.toolbar_focus is ResumeToolbarFocus.SORT,
            ),
        ])
    else:
        parts.extend([
            ("class:resume-picker.toolbar", "Sort: "),
            _toolbar_value(
                "Updated",
                active=state.sort_key is ResumeSortKey.UPDATED,
                focused=state.toolbar_focus is ResumeToolbarFocus.SORT,
            ),
            _toolbar_value(
                "Created",
                active=state.sort_key is ResumeSortKey.CREATED,
                focused=state.toolbar_focus is ResumeToolbarFocus.SORT,
            ),
        ])
    return parts


def _toolbar_value(
    label: str,
    *,
    active: bool,
    focused: bool
) -> tuple[str, str]:
    if not active:
        return "class:resume-picker.toolbar", f" {label} "
    style = "class:resume-picker.toolbar.active"
    if focused:
        style = f"{style} class:resume-picker.toolbar.focused"
    return style, f"[{label}]"


def _list_lines(
    state: ResumePickerState,
    *,
    width: int,
    height: int
) -> list[FormattedText]:
    if height <= 0:
        return []

    content_width = surface_content_width(
        width,
        inset=MENU_SURFACE_HORIZONTAL_INSET,
    )

    rows = filtered_resume_rows(state)
    if not rows:
        message = (
            f'No sessions match "{_safe_text(state.query)}"'
            if state.query
            else "No sessions yet"
        )
        return [
            _inset_list_line([
                ("class:resume-picker.empty", message),
            ], width=width),
            *[_blank_line(width) for _ in range(height - 1)],
        ]

    visible_state = ensure_resume_selection_visible(
        state,
        viewport_rows=height,
        width=width,
    )

    top        = visible_state.scroll_top
    show_above = top > 0
    budget     = height - int(show_above)

    rendered, has_below = _render_visible_rows(
        visible_state,
        rows,
        start=top,
        budget=budget,
        width=content_width,
    )

    if has_below:
        rendered, _ = _render_visible_rows(
            visible_state,
            rows,
            start=top,
            budget=max(0, budget - 1),
            width=content_width,
        )

    lines: list[FormattedText] = []
    if show_above:
        lines.append(_inset_list_line([
            ("class:resume-picker.meta", "↑ more"),
        ], width=width))
    lines.extend(_inset_list_line(line, width=width) for line in rendered)
    if has_below and len(lines) < height:
        lines.append(_inset_list_line([
            ("class:resume-picker.meta", "↓ more"),
        ], width=width))
    while len(lines) < height:
        lines.append(_blank_line(width))
    return lines[:height]


def _render_visible_rows(
    state: ResumePickerState,
    rows: tuple[ResumeRow, ...],
    *,
    start: int,
    budget: int,
    width: int
) -> tuple[list[FormattedText], bool]:
    lines: list[FormattedText] = []
    next_index = start
    for index in range(start, len(rows)):
        row_lines = _render_row_lines(
            state,
            rows[index],
            selected=index == state.selected,
            zebra=index % 2 == 0,
            width=width,
        )
        if state.density is ResumeDensity.COMFORTABLE and index > start:
            row_lines = [[], *row_lines]
        remaining = budget - len(lines)
        if remaining <= 0:
            break
        lines.extend(row_lines[:remaining])
        next_index = index + 1 if len(row_lines) <= remaining else index
        if len(row_lines) > remaining:
            break
    return lines, next_index < len(rows)


def _render_row_lines(
    state: ResumePickerState,
    row: ResumeRow,
    *,
    selected: bool,
    zebra: bool,
    width: int
) -> list[FormattedText]:
    expanded = selected and state.expanded_row_key == row.key
    marker = "⌄ " if expanded else "❯ " if selected else "  "
    row_style = (
        "class:resume-picker.row.selected"
        if selected
        else "class:resume-picker.row.zebra" if zebra else ""
    )
    title_style = (
        "class:resume-picker.title.selected"
        if selected
        else ""
    )
    marker_style = "class:resume-picker.marker" if selected else ""

    if state.density is ResumeDensity.DENSE:
        date = _relative_time(state, _row_sort_timestamp(state, row))
        marker_width = get_cwidth(marker)
        date_text = _column_text(date, 12)
        title_width = max(0, width - marker_width - get_cwidth(date_text))
        lines = [[
            (marker_style, marker),
            ("class:resume-picker.meta", date_text),
            (title_style, clip_text(_safe_text(row.title), width=title_width)),
        ]]
    else:
        lines = [[
            (marker_style, marker),
            (title_style, clip_text(_safe_text(row.title), width=max(0, width - 2))),
        ], *_comfortable_meta_lines(state, row, width=width)]

    if row_style:
        lines = [
            _fit_line(
                _apply_row_style(line, row_style),
                width=width,
                fill_style=row_style,
            )
            for line in lines
        ]
    else:
        lines = [_fit_line(line, width=width) for line in lines]
    if expanded:
        lines.extend(
            _fit_line(line, width=width)
            for line in _expanded_lines(state, row, width=width)
        )
    return lines


def _comfortable_meta_lines(
    state: ResumePickerState,
    row: ResumeRow,
    *,
    width: int
) -> list[FormattedText]:
    """按固定日期/CWD 列拼接紧凑行元数据。"""
    date  = _relative_time(state, _row_sort_timestamp(state, row))
    style = "class:resume-picker.meta"

    fields: list[tuple[str, str, int | None]] = [
        (style, date, SESSION_META_DATE_WIDTH),
    ]
    if state.request.show_workspace or state.filter_mode is ResumeFilterMode.ALL:
        workspace_value = _safe_text(row.workspace)
        workspace = workspace_value or "no cwd"
        cwd_width = _cwd_column_width(width)
        fields.append((
            style if workspace_value else "class:resume-picker.meta.placeholder",
            f"{SESSION_META_CWD_ICON} {workspace}",
            cwd_width,
        ))
    branch_value = _safe_text(row.branch)
    branch = branch_value or "no branch"
    fields.append((
        style if branch_value else "class:resume-picker.meta.placeholder",
        f"{SESSION_META_BRANCH_ICON} {branch}",
        None,
    ))

    lines: list[FormattedText] = []
    current: FormattedText = [("", " " * SESSION_META_INDENT_WIDTH)]
    current_width = SESSION_META_INDENT_WIDTH
    for style_name, value, fixed_width in fields:
        gap = SESSION_META_FIELD_GAP_WIDTH if current_width > SESSION_META_INDENT_WIDTH else 0
        available = max(0, width - current_width - gap)
        if (
            fixed_width is not None
            and fixed_width > available
            and current_width > SESSION_META_INDENT_WIDTH
        ):
            lines.append(_fit_line(current, width=width))
            current = [("", " " * SESSION_META_INDENT_WIDTH)]
            current_width = SESSION_META_INDENT_WIDTH
            gap = 0
            available = max(0, width - current_width)

        if available <= 0:
            lines.append(_fit_line(current, width=width))
            current = [("", " " * SESSION_META_INDENT_WIDTH)]
            current_width = SESSION_META_INDENT_WIDTH
            gap = 0
            available = max(0, width - current_width)

        if fixed_width is not None:
            field_width = min(fixed_width, available)
            field_text = _meta_column_text(value, field_width)
        else:
            field_text = clip_text(value, width=available)
            field_width = get_cwidth(field_text)

        if gap:
            current.append(("", " " * gap))
            current_width += gap
        current.append((style_name, field_text))
        current_width += field_width

    lines.append(_fit_line(current, width=width))
    return lines


def _cwd_column_width(width: int) -> int:
    """计算紧凑行的固定 CWD 列宽。"""
    available = max(
        0,
        int(width)
        - SESSION_META_INDENT_WIDTH
        - SESSION_META_DATE_WIDTH
        - SESSION_META_FIELD_GAP_WIDTH * 2,
    )
    return max(
        SESSION_META_MIN_CWD_WIDTH,
        min(SESSION_META_MAX_CWD_WIDTH, available // 2),
    )


def _expanded_lines(
    state: ResumePickerState,
    row: ResumeRow,
    *,
    width: int,
) -> list[FormattedText]:
    lines = [
        _detail_line("Session:", f"{row.cid} · {row.sid}", width=width),
        _detail_line(
            "Created:",
            _expanded_time(state, row.created_at_ms),
            width=width,
        ),
        _detail_line(
            "Updated:",
            _expanded_time(
                state,
                row.updated_at_ms
                if row.updated_at_ms is not None
                else row.created_at_ms,
            ),
            width=width,
        ),
        _detail_line("Directory:", row.workspace or "-", width=width),
        _detail_line("Branch:", row.branch or "-", width=width),
        _detail_line(
            "Status:",
            row.status.value if isinstance(row.status, ResumeSessionStatus) else row.status,
            width=width,
        ),
        _detail_line("Source:", row.source or "-", width=width),
        [("class:resume-picker.meta", "  │")],
        [("class:resume-picker.meta", "  │ Conversation:")],
    ]
    preview = state.preview
    if preview is None or preview.row_key != row.key:
        return lines
    if preview.status is ResumePreviewStatus.LOADING:
        return [
            *lines,
            [
                ("class:resume-picker.meta", "  └ "),
                ("class:resume-picker.progress", "Loading recent transcript..."),
            ],
        ]
    if preview.status is ResumePreviewStatus.ERROR:
        return [
            *lines,
            [
                ("class:resume-picker.meta", "  └ "),
                (
                    "class:resume-picker.error",
                    _safe_text(preview.error) or "Could not load transcript preview",
                ),
            ],
        ]
    return [*lines, *_preview_lines(preview, width=width)]


def _preview_lines(preview: ResumePreview, *, width: int) -> list[FormattedText]:
    content_width = surface_content_width(
        width,
        inset=MENU_SURFACE_HORIZONTAL_INSET,
    )
    wrapped: list[FormattedText] = []
    for block in preview.blocks:
        fragments = sanitize_formatted_text(list(block))
        wrapped.extend(wrap_formatted_lines(fragments, width=content_width))
    if not wrapped:
        wrapped = [[
            ("class:resume-picker.meta", "No transcript preview available"),
        ]]
    lines: list[FormattedText] = []
    for index, line in enumerate(wrapped):
        connector = "  └ " if index + 1 == len(wrapped) else "  │ "
        lines.append([
            ("class:resume-picker.meta", connector),
            *line,
        ])
    return lines


def _detail_line(label: str, value: typing.Any, *, width: int) -> FormattedText:
    prefix = f"  │ {label:<10}  "
    value_width = max(0, width - get_cwidth(prefix))
    return [
        ("class:resume-picker.meta", prefix),
        ("", clip_text(_safe_text(value), width=value_width)),
    ]


def _footer_lines(
    state: ResumePickerState,
    *,
    width: int,
    list_height: int,
) -> list[FormattedText]:
    rows = filtered_resume_rows(state)
    position = min(state.selected + 1, len(rows)) if rows else 0
    percent = _scroll_percent(
        state,
        rows,
        list_height=list_height,
        width=surface_content_width(
            width,
            inset=MENU_SURFACE_HORIZONTAL_INSET,
        ),
    )
    progress = f" {position} / {len(rows)} · {percent}% "
    progress_width = get_cwidth(progress)
    if progress_width + 1 < width:
        rule_width = width - progress_width - 1
        separator = [
            ("class:resume-picker.rule", "─" * rule_width),
            ("class:resume-picker.rule", progress),
            ("class:resume-picker.rule", "─"),
        ]
    else:
        separator = [("class:resume-picker.rule", "─" * width)]

    density = (
        "dense view"
        if state.density is ResumeDensity.COMFORTABLE
        else "comfortable view"
    )
    if width >= 120:
        primary = (
            ("enter", "resume"),
            ("esc", "exit"),
            ("ctrl+c", "exit"),
            ("tab", "focus sort/filter"),
            ("←/→", "change option"),
        )
        secondary = (
            ("ctrl+o", density),
            ("ctrl+t", "transcript"),
            ("ctrl+e", "expand"),
            ("↑/↓", "browse"),
        )
    elif width >= 48:
        primary = (
            ("enter", "resume"),
            ("esc", "exit"),
            ("ctrl+c", "exit"),
            ("tab", "focus"),
            ("←/→", "option"),
        )
        secondary = (
            (
                ("ctrl+o", "comfy"),
                ("ctrl+t", "preview"),
                ("ctrl+e", "exp"),
                ("↑/↓", "browse"),
            )
            if state.density is ResumeDensity.DENSE
            else (
                ("ctrl+o", "dense"),
                ("ctrl+t", "preview"),
                ("ctrl+e", "exp"),
                ("↑/↓", "browse"),
            )
        )
    else:
        primary = (
            ("↵", ""),
            ("esc", ""),
            ("^c", ""),
            ("tab", ""),
            ("←/→", ""),
        )
        secondary = (("^o", ""), ("^t", ""), ("^e", ""), ("↑/↓", ""))
    action_line = _archive_action_line(state, width=width)
    return [
        _fit_line(separator, width=width),
        _footer_help_line(primary, width=width),
        _footer_help_line(secondary, width=width),
        action_line,
    ]


def _archive_action_line(
    state: ResumePickerState,
    *,
    width: int,
) -> FormattedText:
    """显示归档动作的进行中或失败状态。"""
    if state.archive_error:
        return _fit_line([
            ("class:resume-picker.error", f" {state.archive_error}"),
        ], width=width)
    if state.archive_status is ResumeArchiveStatus.PENDING:
        return _fit_line([
            ("class:resume-picker.meta", " Archiving session..."),
        ], width=width)
    if state.archive_status is ResumeArchiveStatus.RESTORING:
        return _fit_line([
            ("class:resume-picker.meta", " Restoring archived session..."),
        ], width=width)
    return _blank_line(width)


def _footer_help_line(
    items: tuple[tuple[str, str], ...],
    *,
    width: int,
) -> FormattedText:
    """生成快捷键亮色、动作说明暗色的 footer 行。"""
    parts: FormattedText = []
    for index, (key, description) in enumerate(items):
        if index == 0:
            parts.append(("class:resume-picker.help", " "))
        else:
            parts.append(("class:resume-picker.help", "   "))
        parts.append(("class:resume-picker.help.key", key))
        if description:
            parts.append(("class:resume-picker.help", f" {description}"))
    return _fit_line(parts, width=width)


def _scroll_percent(
    state: ResumePickerState,
    rows: tuple[ResumeRow, ...],
    *,
    list_height: int,
    width: int,
) -> int:
    if not rows:
        return 100

    content_rows = max(
        1,
        int(list_height)
        - int(state.scroll_top > 0)
        - int(state.selected + 1 < len(rows)),
    )
    total_height = _rendered_height_between(
        state,
        rows,
        0,
        len(rows) - 1,
        width=max(1, int(width)),
    )
    max_scroll = max(0, total_height - content_rows)
    if max_scroll == 0:
        return 100

    remaining_height = _rendered_height_between(
        state,
        rows,
        min(max(0, state.scroll_top), len(rows) - 1),
        len(rows) - 1,
        width=max(1, int(width)),
    )
    if remaining_height <= content_rows:
        return 100

    skipped_height = (
        _rendered_height_between(
            state,
            rows,
            0,
            state.scroll_top - 1,
            width=max(1, int(width)),
        )
        if state.scroll_top > 0
        else 0
    )
    return round(100 * min(skipped_height, max_scroll) / max_scroll)


def _rendered_height_between(
    state: ResumePickerState,
    rows: tuple[ResumeRow, ...],
    start: int,
    end: int,
    *,
    width: int,
) -> int:
    total = 0
    for index in range(start, end + 1):
        if index > start and state.density is ResumeDensity.COMFORTABLE:
            total += 1
        total += len(_render_row_lines(
            state,
            rows[index],
            selected=index == state.selected,
            zebra=index % 2 == 0,
            width=width,
        ))
    return total


def _row_matches_query(row: ResumeRow, query: str) -> bool:
    return any(
        query in str(value or "").casefold()
        for value in (
            row.title,
            row.cid,
            row.sid,
            row.workspace,
            row.source,
            row.branch,
            row.status.value if isinstance(row.status, ResumeSessionStatus) else row.status,
        )
    )


def _sort_key(
    state: ResumePickerState,
    row: ResumeRow,
) -> tuple[int, int, str]:
    created = row.created_at_ms if row.created_at_ms is not None else -1
    updated = (
        row.updated_at_ms
        if row.updated_at_ms is not None
        else created
    )
    primary = updated if state.sort_key is ResumeSortKey.UPDATED else created
    return primary, created, row.sid


def _row_sort_timestamp(
    state: ResumePickerState,
    row: ResumeRow,
) -> int | None:
    if state.sort_key is ResumeSortKey.CREATED:
        return row.created_at_ms
    return (
        row.updated_at_ms
        if row.updated_at_ms is not None
        else row.created_at_ms
    )


def _clamp_selection(state: ResumePickerState) -> ResumePickerState:
    rows = filtered_resume_rows(state)
    if not rows:
        return replace(state, selected=0, scroll_top=0)
    selected = min(max(0, state.selected), len(rows) - 1)
    return replace(
        state,
        selected=selected,
        scroll_top=min(max(0, state.scroll_top), selected),
    )


def _workspace_key(value: typing.Any) -> str:
    text = str(value or "").strip().replace("\\", "/").rstrip("/")
    if len(text) == 2 and text[1] == ":":
        text += "/"
    if len(text) >= 2 and text[1] == ":":
        return text.casefold()
    return text


def _relative_time(state: ResumePickerState, value: int | None) -> str:
    if value is None:
        return "-"
    seconds = max(0, (state.relative_time_now_ms - int(value)) // 1000)
    if seconds == 0:
        return "now"
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _expanded_time(state: ResumePickerState, value: int | None) -> str:
    if value is None:
        return "-"
    relative = _relative_time_long(state, value)
    try:
        timestamp = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.gmtime(int(value) / 1000),
        )
    except (OverflowError, OSError, ValueError):
        return relative
    return f"{relative} · {timestamp}"


def _relative_time_long(state: ResumePickerState, value: int) -> str:
    seconds = max(0, (state.relative_time_now_ms - int(value)) // 1000)
    if seconds == 0:
        return "now"
    if seconds < 60:
        amount, unit = seconds, "second"
    elif seconds < 3600:
        amount, unit = seconds // 60, "minute"
    elif seconds < 86400:
        amount, unit = seconds // 3600, "hour"
    else:
        amount, unit = seconds // 86400, "day"
    suffix = "" if amount == 1 else "s"
    return f"{amount} {unit}{suffix} ago"


def _column_text(value: str, width: int) -> str:
    text = clip_text(value, width=max(0, width - 1))
    return text + " " * max(0, width - get_cwidth(text))


def _meta_column_text(value: str, width: int) -> str:
    """按固定元数据列宽裁剪并填充文本。"""
    text = clip_text(value, width=max(0, width))
    return text + " " * max(0, width - get_cwidth(text))


def _chrome_line(parts: FormattedText, *, width: int) -> FormattedText:
    return _fit_line([
        ("", " "),
        *clip_fragments(parts, width=max(0, width - 2)),
    ], width=width)


def _inset_list_line(parts: FormattedText, *, width: int) -> FormattedText:
    content_width = surface_content_width(
        width,
        inset=MENU_SURFACE_HORIZONTAL_INSET,
    )
    return _fit_line([
        ("", " " * MENU_SURFACE_HORIZONTAL_INSET),
        *_fit_line(parts, width=content_width),
    ], width=width)


def _fit_line(
    parts: FormattedText,
    *,
    width: int,
    fill_style: str = "",
) -> FormattedText:
    target = max(1, int(width))
    safe = sanitize_formatted_text(parts)
    fitted = clip_fragments(safe, width=target)
    remaining = target - _fragments_width(fitted)
    if remaining > 0:
        fitted.append((fill_style, " " * remaining))
    return fitted


def _blank_line(width: int) -> FormattedText:
    return [("", " " * max(1, int(width)))]


def _apply_row_style(parts: FormattedText, row_style: str) -> FormattedText:
    return [
        (f"{row_style} {style}".strip(), text)
        for style, text in parts
    ]


def _safe_text(value: typing.Any) -> str:
    return fragments_text(sanitize_formatted_text([("", str(value or ""))]))


def _fragments_width(parts: FormattedText) -> int:
    return get_cwidth(fragments_text(parts))


if __name__ == '__main__':
    pass
