# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field


VideoListArg = typing.Annotated[
    list[str],
    Field(description="待分析的视频文件路径列表。"),
]
ReportDirArg = typing.Annotated[
    typing.Optional[str],
    Field(description="Framix 结果目录或报告目录；为空时使用当前默认结果目录。"),
]
ScaleArg = typing.Annotated[
    float,
    Field(description="分析前的缩放比例，用于平衡速度与细节。"),
]
TitleArg = typing.Annotated[
    str,
    Field(description="本次分析任务标题，用于结果目录或报告标识。"),
]


if __name__ == '__main__':
    pass
