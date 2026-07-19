# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_nova import const
from .contracts import OutputPort


def create_output(
    log_file: str,
    *,
    design_level: str = const.SHOW_LEVEL,
) -> OutputPort:
    """创建当前默认的单轮输出端。"""
    from mind_app.stream_ui import StreamUI

    return StreamUI(log_file, design_level=design_level)


if __name__ == '__main__':
    pass
