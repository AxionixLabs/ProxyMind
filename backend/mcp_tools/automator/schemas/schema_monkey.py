# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field


SeedArg = typing.Annotated[
    int,
    Field(description="monkey 随机种子；相同参数下有助于复现实验。")
]
ThrottleArg = typing.Annotated[
    int,
    Field(description="两次事件之间的间隔，单位毫秒。")
]
TouchPctArg = typing.Annotated[
    int,
    Field(description="touch 事件占比。")
]
MotionPctArg = typing.Annotated[
    int,
    Field(description="motion 事件占比。")
]
NavPctArg = typing.Annotated[
    int,
    Field(description="导航类事件占比。")
]
EventsArg = typing.Annotated[
    int,
    Field(description="总事件数。")
]


if __name__ == '__main__':
    pass
