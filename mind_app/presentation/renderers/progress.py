# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.application.views import ProgressView
from agent.ports.presentation import StyledBlock


def render_progress_view(view: ProgressView) -> StyledBlock:
    """把工具进度视图转换为无样式展示块。"""
    return StyledBlock(plain_text=view.text)


if __name__ == '__main__':
    pass
