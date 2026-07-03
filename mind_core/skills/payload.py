# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from .registry import configured_skills


def skills_payload() -> list[dict[str, str]]:
    """返回每轮请求携带的可用 skills 轻量描述。"""
    return [skill.payload() for skill in configured_skills()]


if __name__ == '__main__':
    pass
