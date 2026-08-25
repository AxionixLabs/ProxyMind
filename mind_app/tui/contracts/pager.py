# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass
from .text import FormattedLine


@dataclass(frozen=True, slots=True)
class StaticPagerRequest(object):
    """描述一次只读全屏静态页面请求。"""
    title: str
    lines: tuple[FormattedLine, ...]


if __name__ == '__main__':
    pass
