# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from frontends.tui.contracts.text import (
    FragmentBlock,
    LineFill
)

FINAL_SEPARATOR_CHAR = "─"
FINAL_SEPARATOR_STYLE = "dim"


def _elapsed_label(elapsed_sec: float | None) -> str:
    if elapsed_sec is None:
        return ""

    elapsed_seconds = max(0, int(float(elapsed_sec)))
    if elapsed_seconds <= 60:
        return ""
    if elapsed_seconds < 3_600:
        minutes, seconds = divmod(elapsed_seconds, 60)
        return f"{minutes}m {seconds:02d}s"

    hours, remainder = divmod(elapsed_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m {seconds:02d}s"


def final_message_separator(elapsed_sec: float | None = None) -> FragmentBlock:
    label = _elapsed_label(elapsed_sec)

    text = (
        f"{FINAL_SEPARATOR_CHAR} Finished in {label} {FINAL_SEPARATOR_CHAR}"
        if label
        else FINAL_SEPARATOR_CHAR
    )

    return FragmentBlock(
        fragments=((FINAL_SEPARATOR_STYLE, text),),
        line_fill=LineFill(character=FINAL_SEPARATOR_CHAR),
    )


if __name__ == '__main__':
    pass
