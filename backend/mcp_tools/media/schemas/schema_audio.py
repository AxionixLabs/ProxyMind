# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field


AudioFileArg = typing.Annotated[
    str,
    Field(description="要在当前运行环境本机播放的音频文件路径。")
]
VolumeArg = typing.Annotated[
    float,
    Field(description="播放音量倍率，`1.0` 表示原始音量。")
]


if __name__ == '__main__':
    pass
