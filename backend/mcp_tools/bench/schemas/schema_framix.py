# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field

VideoListArg = typing.Annotated[
    list[str],
    Field(description="待分析的视频文件路径列表。"),
]
TotalDirArg = typing.Annotated[
    str,
    Field(description="Framix 结果根目录。"),
]
LabelArg = typing.Annotated[
    str,
    Field(
        pattern=r"^\d{14}$",
        description="压缩时间戳（格式：YYYYMMDDhhmmss），用于唯一标识任务。",
    ),
]
ReportDirArg = typing.Annotated[
    str,
    Field(description="待生成汇总报告的 Framix 分析结果目录。"),
]
ScaleArg = typing.Annotated[
    float,
    Field(description="分析前的缩放比例，用于平衡速度与细节。"),
]
TitleArg = typing.Annotated[
    str,
    Field(description="本次分析任务标题，用于报告展示。"),
]


if __name__ == '__main__':
    pass
