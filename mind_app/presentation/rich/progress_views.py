# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from ..models import ProgressView
from .models import RenderedBlock


def render_progress_view(view: ProgressView) -> RenderedBlock:
    """把工具进度视图转换为当前无样式块输出。"""
    return RenderedBlock(
        text=view.text,
        display_parts=None,
    )


if __name__ == '__main__':
    pass
