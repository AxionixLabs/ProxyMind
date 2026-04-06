# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field


ScriptTextArg = typing.Annotated[
    typing.Optional[str],
    Field(description="脚本文本。"),
]
ScriptFileArg = typing.Annotated[
    typing.Optional[str],
    Field(description="本地脚本文件路径。"),
]
ScriptNameArg = typing.Annotated[
    typing.Optional[str],
    Field(description="脚本文件名；为空时自动使用默认名。"),
]
WorkDirArg = typing.Annotated[
    typing.Optional[str],
    Field(description="运行目录；为空时使用脚本所在目录。"),
]
VusArg = typing.Annotated[
    typing.Optional[int],
    Field(description="并发执行规模；为空时沿用脚本内配置。"),
]
DurationArg = typing.Annotated[
    typing.Optional[str],
    Field(description="执行时长，如 30s、5m；为空时沿用脚本内配置。"),
]
IterationsArg = typing.Annotated[
    typing.Optional[int],
    Field(description="总执行次数；为空时沿用脚本内配置。"),
]
EnvArg = typing.Annotated[
    typing.Optional[dict[str, str]],
    Field(description="注入给脚本的环境变量。"),
]
TagsArg = typing.Annotated[
    typing.Optional[dict[str, str]],
    Field(description="附加到本次结果的标签。"),
]
SummaryExportArg = typing.Annotated[
    typing.Optional[str],
    Field(description="汇总结果 JSON 输出路径；为空时自动生成。"),
]
ExtraArgsArg = typing.Annotated[
    typing.Optional[list[str]],
    Field(description="附加透传的额外执行参数；每项都应是单独的 CLI token。"),
]


if __name__ == '__main__':
    pass
