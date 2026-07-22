# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import typing
from pydantic import Field

FocusArg = typing.Annotated[
    str,
    Field(description="采样目标，一般是包名、进程名或业务焦点标识。"),
]
ImplyArg = typing.Annotated[
    typing.Optional[str],
    Field(description="附加提示信息，用于帮助 Memrix 更准确定位采样目标。"),
]
TaskTitleArg = typing.Annotated[
    typing.Optional[str],
    Field(description="采样任务标题，用于报告显示。"),
]
TokenArg = typing.Annotated[
    typing.Optional[str],
    Field(description="要结束的 Memrix 采样任务 token；为空时使用当前会话中的最近任务。"),
]
SampleSceneArg = typing.Annotated[
    str,
    Field(
        min_length=1,
        description="Memrix 采样任务的输出目录或任务目录前缀。",
    ),
]
ReportSceneArg = typing.Annotated[
    str,
    Field(
        min_length=1,
        description="采样工具返回的 `data.report_scene`，用于指定已有采样结果目录。",
    ),
]
LayerArg = typing.Annotated[
    bool,
    Field(description="是否输出更细的分层视图。"),
]


if __name__ == '__main__':
    pass
