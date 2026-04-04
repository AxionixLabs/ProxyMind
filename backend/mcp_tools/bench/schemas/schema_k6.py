# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field


ScenarioArg = typing.Annotated[
    dict[str, typing.Any],
    Field(
        description=(
            "结构化压测场景。"
            "建议包含 `name`、`base_url`、`steps`；"
            "`steps[]` 里的常用字段有 `name`、`method`、`path`、`params`、`headers`、`body`、`checks`、`sleep_sec`。"
        )
    ),
]
ScriptFileArg = typing.Annotated[
    str,
    Field(description="k6 脚本文件路径，通常是 .js 或 .ts 文件。"),
]
WorkDirArg = typing.Annotated[
    typing.Optional[str],
    Field(description="运行目录；为空时使用脚本所在目录。"),
]
VusArg = typing.Annotated[
    typing.Optional[int],
    Field(description="虚拟用户数；为空时沿用 `scenario.options` 或脚本内配置。"),
]
DurationArg = typing.Annotated[
    typing.Optional[str],
    Field(description="压测持续时间，如 30s、5m；为空时沿用 `scenario.options` 或脚本内配置。"),
]
IterationsArg = typing.Annotated[
    typing.Optional[int],
    Field(description="总迭代次数；为空时沿用 `scenario.options` 或脚本内配置。"),
]
EnvArg = typing.Annotated[
    typing.Optional[dict[str, str]],
    Field(description="以 `-e KEY=VALUE` 方式注入给 k6 脚本的环境变量。"),
]
TagsArg = typing.Annotated[
    typing.Optional[dict[str, str]],
    Field(description="附加到本次压测结果的 k6 标签。"),
]
SummaryExportArg = typing.Annotated[
    typing.Optional[str],
    Field(description="summary JSON 输出路径；为空时不导出。"),
]
ExtraArgsArg = typing.Annotated[
    typing.Optional[list[str]],
    Field(description="附加透传给 `k6 run` 的额外参数；每项都应是单独的 CLI token。"),
]


if __name__ == '__main__':
    pass
