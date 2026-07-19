# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_app.presentation.plan_views import build_plan_steps_start_view
from mind_app.presentation.rich import render_plan_steps_start_view


def render_plan_steps_start(
    arguments: typing.Any,
) -> tuple[str, list[dict[str, typing.Optional[str]]]]:
    """将计划步骤参数转换为当前终端展示。"""
    rendered = render_plan_steps_start_view(
        build_plan_steps_start_view(arguments)
    )
    return rendered.text, list(rendered.display_parts)


if __name__ == '__main__':
    pass
