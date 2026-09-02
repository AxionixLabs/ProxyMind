# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from ..rendering.fragments import (
    ZERO_WIDTH_ESCAPE_STYLE,
    clip_fragments,
    clip_text,
    cursor_point,
    cursor_point_for_display_row,
    display_line_count,
    fill_fragments,
    fragment_continuation_widths,
    fragments_text,
    iter_formatted_text_units,
    iter_text_unit_ranges,
    iter_text_units,
    join_formatted_lines,
    next_text_unit_end,
    split_formatted_lines,
    transcript_hint,
    wrap_formatted_lines,
)
from ..rendering.text_sanitize import (
    OSC8_PREFIX,
    OSC8_SUFFIX,
    sanitize_formatted_text,
    sanitize_fragment_block,
)

__all__ = (
    "ZERO_WIDTH_ESCAPE_STYLE",
    "clip_fragments",
    "clip_text",
    "cursor_point",
    "cursor_point_for_display_row",
    "display_line_count",
    "fill_fragments",
    "fragment_continuation_widths",
    "fragments_text",
    "iter_formatted_text_units",
    "iter_text_unit_ranges",
    "iter_text_units",
    "join_formatted_lines",
    "next_text_unit_end",
    "split_formatted_lines",
    "transcript_hint",
    "wrap_formatted_lines",
    "OSC8_PREFIX",
    "OSC8_SUFFIX",
    "sanitize_formatted_text",
    "sanitize_fragment_block",
)


if __name__ == '__main__':
    pass
