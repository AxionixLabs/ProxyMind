# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.application.views.contracts import PresentationSink
from agent.application.views import ProgressSource
from agent.application.views.builders.progress import build_progress_view


async def show_tool_progress(
    presentation: PresentationSink,
    text: typing.Any,
    *,
    source: ProgressSource,
    tool_name: str,
) -> None:
    """发送工具执行期间的结构化进度数据。"""
    view = build_progress_view(
        text,
        source=source,
        tool_name=tool_name,
    )
    if view is not None:
        await presentation.emit(view)


if __name__ == '__main__':
    pass
