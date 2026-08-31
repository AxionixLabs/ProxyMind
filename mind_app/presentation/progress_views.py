# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.application.views import (
    ProgressSource,
    ProgressView
)


def build_progress_view(
    text: typing.Any,
    *,
    source: ProgressSource,
    tool_name: str,
) -> ProgressView | None:
    """构建工具执行期间的结构化进度数据。"""
    normalized_text = str(text or "")
    if not normalized_text:
        return None

    return ProgressView(
        text=normalized_text,
        source=source,
        tool_name=str(tool_name or "tool").strip() or "tool",
    )


if __name__ == '__main__':
    pass
