# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====


def segment_prefix(
    *,
    last_display: str | None,
    trailing_newlines: int,
    stream_display: str,
    for_display: str,
    incoming_text: str | None = None
) -> str:
    """返回下一段流式输出前需要补充的段间前缀。"""
    if last_display is None:
        return ""

    if last_display == stream_display and for_display == stream_display:
        return ""

    trailing = max(0, int(trailing_newlines or 0))
    if trailing >= 2:
        return ""

    if for_display == stream_display and incoming_text and incoming_text.startswith("\n"):
        return ""

    return "\n" * (2 - trailing)


if __name__ == '__main__':
    pass
