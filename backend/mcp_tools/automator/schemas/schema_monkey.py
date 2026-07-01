# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field

SeedArg = typing.Annotated[
    int,
    Field(description="monkey 随机种子，用于复现同类运行。")
]
ThrottleArg = typing.Annotated[
    int,
    Field(description="事件间隔，单位毫秒。")
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
    Field(description="导航事件占比。")
]
EventsArg = typing.Annotated[
    int,
    Field(description="总事件数。")
]
MonkeySavedPathArg = typing.Annotated[
    typing.Optional[str],
    Field(description="会话结束后导出 logcat 的根目录；为空则不落盘。")
]
GuardForegroundArg = typing.Annotated[
    bool,
    Field(description="运行中是否守护目标应用前台；默认开启。")
]
GuardIntervalArg = typing.Annotated[
    float,
    Field(description="前台守护轮询间隔，单位秒。")
]
GuardStartupGraceArg = typing.Annotated[
    float,
    Field(description="冷启动宽限期，单位秒；默认 3。")
]
GuardMissThresholdArg = typing.Annotated[
    int,
    Field(description="连续失焦多少次后触发守护动作；默认 1。")
]
GuardActionArg = typing.Annotated[
    typing.Literal["observe", "stop", "fail"],
    Field(description="失焦后的动作：仅观察、停止，或失败；默认仅观察。")
]


if __name__ == '__main__':
    pass
