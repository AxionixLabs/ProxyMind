# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field


KeyCodeArg = typing.Annotated[
    int,
    Field(description="Android keycode。")
]
KeyCodeListArg = typing.Annotated[
    list[int],
    Field(description="需要与 `first` 组合触发的附加 keycode 列表。")
]
RebootModeArg = typing.Annotated[
    typing.Literal["", "recovery", "bootloader", "edl"],
    Field(description="重启目标模式；空字符串表示普通重启。")
]
WaitReconnectArg = typing.Annotated[
    bool,
    Field(description="普通重启后是否等待设备重新回到 adb online。")
]
WaitTimeoutArg = typing.Annotated[
    float,
    Field(description="等待设备重新上线的超时时间，单位秒。")
]
ToggleArg = typing.Annotated[
    bool,
    Field(description="目标开关状态。")
]


if __name__ == '__main__':
    pass
