# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_nova import const
from .session import OutputSession


def create_output_session(
    log_file: str,
    *,
    design_level: str = const.SHOW_LEVEL,
) -> OutputSession:
    """创建使用当前终端行为的单轮输出会话。"""
    from mind_app.output.legacy_content import LegacyContentSink
    from mind_app.presentation.legacy import LegacyPresentationSink
    from mind_app.stream_ui import StreamUI

    control = StreamUI(log_file, design_level=design_level)

    return OutputSession(
        control=control,
        content=LegacyContentSink(control),
        presentation=LegacyPresentationSink(control)
    )


if __name__ == '__main__':
    pass
