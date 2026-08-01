# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field

RecordDirectoryArg = typing.Annotated[
    str,
    Field(
        min_length=1,
        pattern=r"\S",
        description="录屏文件保存目录；多设备时每台设备会生成独立文件。",
    )
]
RecordFpsArg = typing.Annotated[
    int,
    Field(description="scrcpy 录屏目标帧率。")
]
SilenceArg = typing.Annotated[
    bool,
    Field(description="是否以静默方式启动录制。")
]


if __name__ == '__main__':
    pass
