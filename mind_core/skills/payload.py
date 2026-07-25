# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .registry import configured_skills


def skills_payload(
    config: dict[str, typing.Any],
) -> list[dict[str, str]]:
    """返回每轮请求携带的可用 skills 轻量描述。"""
    return [skill.payload() for skill in configured_skills(config)]


if __name__ == '__main__':
    pass
