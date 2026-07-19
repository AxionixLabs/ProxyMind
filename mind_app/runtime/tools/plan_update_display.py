# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.presentation.plan_views import build_plan_update_view
from mind_app.presentation.rich import render_plan_update_view


def render_plan_update(
    data: typing.Any,
) -> tuple[str, list[dict[str, typing.Optional[str]]]] | None:
    """将计划工具结果转换为当前终端展示。"""
    view = build_plan_update_view(data)
    if view is None:
        return None

    rendered = render_plan_update_view(view)
    return rendered.text, list(rendered.display_parts)


if __name__ == '__main__':
    pass
