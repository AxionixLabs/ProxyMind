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
    Field(description="脚本文件名；为空时自动生成。"),
]
WorkDirArg = typing.Annotated[
    typing.Optional[str],
    Field(description="运行目录；为空时使用脚本目录。"),
]
VusArg = typing.Annotated[
    typing.Optional[int],
    Field(description="并发数；为空时沿用脚本内配置。"),
]
DurationArg = typing.Annotated[
    typing.Optional[str],
    Field(description="执行时长，如 `30s`、`5m`；为空时沿用脚本内配置。"),
]
IterationsArg = typing.Annotated[
    typing.Optional[int],
    Field(description="总执行次数；为空时沿用脚本内配置。"),
]
EnvArg = typing.Annotated[
    typing.Optional[dict[str, str]],
    Field(description="脚本环境变量。"),
]
TagsArg = typing.Annotated[
    typing.Optional[dict[str, str]],
    Field(description="结果标签。"),
]
SummaryExportArg = typing.Annotated[
    typing.Optional[str],
    Field(description="汇总 JSON 输出路径；为空时自动生成。"),
]
ExtraArgsArg = typing.Annotated[
    typing.Optional[list[str]],
    Field(description="附加执行参数；每项为一个独立 token。"),
]
ExecutionModeArg = typing.Annotated[
    typing.Optional[typing.Literal["auto", "load", "probe"]],
    Field(description="执行模式：`auto`、`load` 或 `probe`。仅对 `perf_run` 生效。"),
]
ResponseCaptureArg = typing.Annotated[
    typing.Optional[typing.Literal["auto", "on", "off"]],
    Field(description="响应采集：`auto`、`on` 或 `off`。仅对 `perf_run` 生效。"),
]
ResponseExportArg = typing.Annotated[
    typing.Optional[str],
    Field(description="响应结果 JSON 输出路径；为空时默认落到 summary 同目录。仅对 `perf_run` 生效。"),
]


if __name__ == '__main__':
    pass
