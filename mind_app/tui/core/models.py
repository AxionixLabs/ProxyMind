# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MenuOption(object):
    """描述运行期选择菜单中的一项。"""

    value: typing.Any
    label: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class MenuRequest(object):
    """描述运行期内嵌选择菜单。"""

    title: str
    options: tuple[MenuOption, ...] = ()
    body: tuple[str, ...] = ()
    selected: int = 0
    status: str = ""
    help_text: str = "Up/Down select · Enter apply · Esc cancel"


if __name__ == '__main__':
    pass
